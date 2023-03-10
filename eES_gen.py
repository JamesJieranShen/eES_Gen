# v + e -> v + e (eES) elastic scattering event generator, using event rates generated by snowglobes.
# J. Shen, Feb. 2023 <jierans@sas.upenn.edu>


import numpy as np
from scipy.spatial.transform import Rotation as R
import os

topDir = os.path.dirname(os.path.realpath(__file__))
dataDir = topDir + '/data'

class eES_Gen:
    """
    Class for generating electron events from Snowglobes data.
    Example usage:
    gen = eES_Gen()
    event = gen.genEvent()
    The generated event is in the form of a dictionary with the following keys:
    'nuEnergy' : float
        The neutrino energy in MeV.
    'sn_direction' : np.ndarray
        The direction of the supernova.
    'nuFlavor' : str   
        The neutrino flavor. Accepted values are 'nue', 'nuebar', 'numu', 'numubar', 'nutau', 'nutaubar'.
    'eKE' : float
        The electron kinetic energy in MeV.
    'eDir' : np.ndarray
        The electron direction.
    """
    flavors: list # Neutrino flavors
    nuEnergyBins: np.ndarray # Neutrino energy bins in MeV
    eventRates: dict # Event rates per energy bin, keyed by flavor
    totalRates: dict # Total event rates, keyed by flavor

    def __init__(self, 
                 dataDir=dataDir,
                 flux='gvkm',
                 detectorConfig ='scint20kt',
                 seed=-1):
        self.flavors=['nue', 'nuebar', 'numu', 'numubar', 'nutau', 'nutaubar']
        filenames = [f'{dataDir}/{flux}_{flavor}_e_{detectorConfig}_events.dat' for flavor in self.flavors]
        snowglobesData = [eES_Gen.readSnowglobesTxtFile(filename) for filename in filenames]
        self.nuEnergyBins = snowglobesData[0][0]
        self.eventRates = {flavor: snowglobesData[i][1] for i, flavor in enumerate(self.flavors)}
        self.totalRates = {flavor: snowglobesData[i][2] for i, flavor in enumerate(self.flavors)}
        if seed == -1:
            self.rng = np.random.default_rng()
        else:
            self.rng = np.random.default_rng(seed)

    def getEventRate(self, flavor, nuEnergy):
        """
        Returns the event rate for a given flavor and neutrino energy.
        Parameters
        ----------
        flavor : str
            The neutrino flavor.
        nuEnergy : float
            The neutrino energy in MeV.
        Returns
        -------
        eventRate : float
            The event rate.
        """
        assert flavor in self.flavors, f'Flavor {flavor} not recognized.'
        eventRate = np.interp(nuEnergy, self.nuEnergyBins, self.eventRates[flavor])
        return eventRate
    
    def getTotalRate(self, flavor):
        return self.totalRates[flavor]
    
    def selectNu(self, flavor=None, nuThreshold=5):
        """
        Selects a neutrino flavor and energy from the Snowglobes data.
        Parameters
        ----------
        flavor : str, optional
            The neutrino flavor. If None, a random flavor is selected.
        Returns
        -------
        flavor : str
            The neutrino flavor.
        nuEnergy : float
            The neutrino energy in MeV.
        """
        if flavor is None:
            flavorWeights = np.array([self.totalRates[flavor] for flavor in self.flavors])
            flavorWeights = flavorWeights / np.sum(flavorWeights)
            flavor = self.rng.choice(self.flavors, p=flavorWeights)
        assert flavor in self.flavors, f'Flavor {flavor} not recognized.'

        maxEventRate = np.max(self.eventRates[flavor])
        minEnergy = max(np.min(self.nuEnergyBins), nuThreshold)
        nuEnergy = self.rejectionSampling(lambda x: self.getEventRate(flavor, x), 
                                             minEnergy, np.max(self.nuEnergyBins), 
                                             maxEventRate)
        return flavor, nuEnergy
    
    def genEvent(self, sn_direction=None, flavor=None, eThreshold=1, nuThreshold=2):
        """
        Generates an event.
        Parameters
        ----------
        sn_direction : np.ndarray, optional
            The direction of the supernova in the detector frame. If None, a random direction is selected.
        flavor : str, optional
            The neutrino flavor. If None, a random flavor is selected.
        eThreshold : float, optional
            The threshold for the electron energy in MeV. Default is 1 MeV.
        Returns
        -------
        event : dict
            The event dictionary.
        """
        genFlavor, nuEnergy = self.selectNu(flavor, nuThreshold)
        if sn_direction is None:
            sn_direction = self.rng.uniform(-1, 1, size=3)
        sn_direction = sn_direction / np.linalg.norm(sn_direction)
        # Rotation from z axis to the SN direction
        zaxis = np.array([0, 0, 1])
        rotAxis = np.cross(zaxis, sn_direction)
        rotAxis = rotAxis / np.linalg.norm(rotAxis)
        rotAngle = np.arccos(np.dot(sn_direction, zaxis))
        rot = R.from_rotvec(rotAngle * rotAxis)
        # TODO potential optimization to generate multiple events at once, saving on computing the rotation matrix
        # Generate electron information
        # Details from DUNE docDB: 27538
        WMA = 0.23122 # Weak Mixing Angle sin(theta_w)^2, https://pdg.lbl.gov/2007/reviews/consrpp.pdf
        me = 0.510998910 # Electron mass in MeV, same source as WMA
        if genFlavor == 'nue':
            ga = 0.5
            gv = 2*WMA + 1/2
        elif genFlavor == 'nuebar':
            ga = -0.5
            gv = 2*WMA + 1/2
        elif genFlavor == 'numu' or genFlavor == 'nutau':
            ga = -0.5
            gv = 2*WMA - 1/2
        else: # nuxbar
            ga = 0.5
            gv = 2*WMA - 1/2
        
        def diff_xscn(T):
            # Ignoring a normalization factor of G_F^2 * m_e / (2 pi)
            return (gv + ga)**2 \
                + (gv - ga)**2 * (1 - T/nuEnergy)**2 \
                + (ga**2 - gv**2) * (me*T/(nuEnergy**2))
        maxDiffXscn = diff_xscn(0)
        # Max KE derivation: https://courses.physics.ucsd.edu/2017/Spring/physics4e/compton.pdf
        maxKE = 2*nuEnergy**2/(me + 2*nuEnergy)
        eKE = self.rejectionSampling(diff_xscn, eThreshold, maxKE, maxDiffXscn) 
        eCos = (nuEnergy + me) / nuEnergy * (eKE / (eKE + 2*me))**0.5 
        assert eCos >= -1 and eCos <= 1, f'eCos is {eCos}, T = {eKE}, Ev = {nuEnergy}' # sanity check, can remove
        ePhi = self.rng.uniform(0, 2*np.pi)
        eDir = np.array([np.sqrt(1 - eCos**2) * np.cos(ePhi), 
                        np.sqrt(1 - eCos**2) * np.sin(ePhi), 
                        eCos])
        eDir = rot.apply(eDir)
        assert np.isclose(np.dot(eDir, sn_direction), eCos), f'Dot Product is {np.dot(eDir, sn_direction)}, eCos is {eCos}, T = {eKE}, Ev = {nuEnergy}' # sanity check, can remove

        event = {'flavor': genFlavor,
                 'nuEnergy': nuEnergy,
                 'sn_direction': sn_direction,
                 'eKE': eKE,
                 'eDir': eDir}
        return event

    def rejectionSampling(self, func, xmin, xmax, ymax):
        """
        Performs rejection sampling on a function.
        Parameters
        ----------
        func : function
            The function to sample.
        xmin : float
            The minimum value of the function.
        xmax : float
            The maximum value of the function.
        ymax : float
            The maximum value of the function.
        Returns
        -------
        x : float
            The sampled value.
        """
        nIter = 0
        while True:
            x = self.rng.uniform(xmin, xmax)
            y = self.rng.uniform(0, ymax)
            if y < func(x):
                break
            nIter += 1
            if nIter > 1000:
                raise RuntimeError('Rejection sampling failed to converge.')
        return x

    @staticmethod
    def readSnowglobesTxtFile(fileLocation):
        """
        Reads a Snowglobes output file and returns the neutrino energy, event rates, and total event rate.
        Parameters
        ----------
        fileLocation : str
            The location of the Snowglobes output file.
        Returns
        -------
        nuEnergy : np.ndarray
            The neutrino energy in MeV.
        eventRates : np.ndarray
            The event rates per energy bin.
        totalRate : float
            The total event rate.
        """
        assert os.path.isfile(fileLocation), f'{fileLocation} does not exist.'
        data = np.genfromtxt(fileLocation, autostrip=True, comments='--')
        diffRates = data[:-1]
        totalRate  = data[-1]
        assert np.isnan(totalRate[0]), 'Last line does not have the expected format. Data could be lost/corrupted.'
        totalRate = totalRate[1]
        nuEnergy = diffRates[:,0] * 1e3 # Convert to MeV
        eventRates = diffRates[:,1]

        return nuEnergy, eventRates, totalRate


