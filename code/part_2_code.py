"""

"""

import numpy as np 
from matplotlib import pyplot as plt 
from scipy import interpolate
from scipy import integrate
import pandas as pd

#Define combuster inlet conditions (in burner gas constant terms)
Ru   = 8.314
pRef = 101.3

yb = 1.3205 # gamma
Rb = 188.45 # Gas constant [J/kg K]
M3b = 3.814 # mach number
p3b = 70.09 # static pressure [kPa]
#pt3b = p3b / (1 + 0.5*(yb - 1)*M3b**2)**(-yb/(yb-1)) #stagnation pressure
T3b = 1237.63 # temperature [K]
Tt3b = T3b * (1 + 0.5*(yb - 1) * M3b**2) # stagnation temperature
mdot = 31.1186 # combined mass flow rate of stoichiometric mixture of ethylene and air [kg/s]
cpb = Rb / (1 - 1/yb) # specific heat at constant pressure
rho3b = p3b / (Rb * T3b)
V3b = M3b * np.sqrt(yb * Rb * T3b)
A3 = mdot / rho3b*V3b
combustor_length = 0.5 # m

YN2 = 0.8 #mass fraction of nitrogen

increments = 1000
dx = combustor_length/increments

Cf = 0.002 # skin friction coefficient

MW = np.array([28, 32, 28, 18, 44])



#read chemical data
MW = np.array([28, 32, 28, 18, 44])
chemData = []
for species in ("C2H4", "O2", "CO", "H2O", "CO2"):
    data = pd.read_csv(f"code/chemData/{species}.txt", sep="\t", skiprows=1)
    chemData.append(data[1:])  # Skip T=0K

logKfuncs, deltaHfuncs = [], []
for data in chemData:
    T      = data["T(K)"].values.astype(float)
    logKf  = data["log Kf"].values.astype(float)
    deltaH = data["delta-f H"].values.astype(float) * 1e+03  # kJ/mol->kJ/kmol
    logKfuncs.append(interpolate.interp1d(T, logKf, kind="quadratic"))
    deltaHfuncs.append(interpolate.interp1d(T, deltaH, kind="quadratic"))


def A(x, A3, Length=0.5):
    return A3 * (1 + 3*x/Length)

def dAonA(x, A3, Length=0.5):
    return 3 * A3 / (Length * A(x, A3))

def arrhenius(T):
    return np.array([
        1.739e+09 * np.exp(-1.485e+05 / (Ru*T)),
        6.324e+07 * np.exp(-5.021e+04 / (Ru*T))
    ])

def Y(X):
    return X * MW * (1 - YN2) / ( np.sum(X[0:5] * MW[0:5]) )

def vectorInterface(lengths):
    L = [0, *np.cumsum(lengths)]

    def wrapper(func):
        def inner(t, args):
            splitArgs = [args[l:r] for l, r in zip(L[:-1], L[1:])]
            output = func(t, *splitArgs)
            return np.hstack([*output])
        return inner
    return wrapper

def dXdx(M, Tt, X, T):
    #  (row) species 0 :: C2H4
    #                1 ::   O2
    #                2 ::   CO
    #                3 ::  H2O
    #                4 ::  CO2

    # (col) reaction 0 :: C2H4 + 2 O2 --> 2 CO + 2 H2O
    #                1 :: CO + 1/2 O2 <-> CO2

    # Stoichiometric coefficients
    μ = np.array([
        [-1.,  0. ],
        [-2., -0.5],
        [ 2., -1. ],
        [ 2.,  0. ],
        [ 0.,  1. ]
    ]).T

    # Experimental partial powers
    ν = np.array([
        [0.5 , 0. ],
        [0.65, 0.5],
        [2.  , 1. ],
        [2.  , 0. ],
        [0.  , 1. ]
    ]).T

    # Forward and reverse masks
    maskF = np.zeros((2, 5), dtype=bool)
    maskR = np.zeros((2, 5), dtype=bool)

    maskF[0, (0, 1)] = True  # {C2H4,  O2}
    maskR[0, (2, 3)] = True  # {  CO, H2O}

    maskF[1, (1, 2)] = True  # {  CO, O2}
    maskR[1, (4)]    = True  # { CO2}

    def Kc(T):
        """Kc = Kp * pow(pRef/p, ν+...)"""
        # NOTE: Account for partial pressures
        Kf_i    = np.array([pow(10, f(np.float64(T))) for f in logKfuncs]) * (pRef/(Ru*T))
        forward = pow(Kf_i, maskF*ν)
        reverse = pow(Kf_i, maskR*ν)
        return np.prod(reverse, axis=1) / np.prod(forward, axis=1)

    #@vectorInterface((5,1,1))
    def concentration_gradient(χ, M, Tt, T):
        limit = (χ < 0)
        χ[limit] = 0

        kf    = arrhenius(T)
        kr    = kf / Kc(T)
        kr[0] = 0  # One way reaction

        forward = kf * np.prod(pow(χ, maskF*ν), axis=1)
        reverse = kr * np.prod(pow(χ, maskR*ν), axis=1)
        χGrad   = μ.T @ forward - μ.T @ reverse

        χGrad[(χGrad < 0)*limit] = 0

               
        #hGrad = -sum([dχ_i*h_i(T) for dχ_i, h_i in zip(χGrad, deltaHfuncs)])
        return χGrad

    
    v = M * np.sqrt(yb * Rb * T)
    return concentration_gradient(X, M, Tt, T) / v

def dYdx(X, M, Tt, x, T):
    reacting_sum = np.sum(X * MW)
    return MW * (1 - YN2) * ( 1/reacting_sum * dXdx(M, Tt, X, T) - X/reacting_sum**2 * np.sum(MW[0:5] * dXdx(M, Tt, X, T)))

def dTtdx(X, M, Tt, x, T):
    h0fi = [np.float64(deltaHfuncs[i](T)) for i in range(5)]
    temp_gradient = -1/cpb * np.sum(dYdx(X, M, Tt, x, T) * h0fi)

    return temp_gradient

def dM2dx(M, X, x, Tt, T):
    Deff = 2 * np.sqrt(A(x, A3) / np.pi)
    return M**2 * ((1 + 0.5*(yb - 1)*M**2) / (1 - M**2)) * (-2 * dAonA(x, A3) + (1 + yb*M**2) * dTtdx(X, M, Tt, x, T)/Tt + yb*M**2 * 4 * Cf / Deff)

@vectorInterface((5,1,1))
def gradient(x, X, Tt, M2):
    x = np.float64(x)
    Tt = np.float64(Tt)
    M = np.sqrt(np.float64(M2))
    T = Tt * (1 + 0.5*(yb - 1) * M**2)**(-1)
    if Tt > 1.15 * Tt3b:
        print("combustion begins at x = ", x, "m")
    return [dXdx(M, Tt, X, T), dTtdx(X, M, Tt, x, T), dM2dx(M, X, x, Tt, T)]

n = 1 + 3*(1 + 3.76)
X3 = np.array(
        [1/n, 3/n, 0.0, 0.0, 0.0]
    ) * 70e+03 / (Ru * T3b) * 1e-03

init_conds = np.append(X3, [Tt3b, M3b**2])

#for x in np.linspace(0, combustor_length, increments): 
sol = (integrate.solve_ivp(gradient, (0, 0.5), init_conds, method="LSODA", events=None, atol=1e-10, rtol=1e-10))


x, y = sol.t, sol.y


plt.plot(x, y[-2], label = "Tt")
plt.xlabel("x [m]")
plt.ylabel("$T_t$ [K]")
plt.legend()
plt.show()

fig, ax = plt.subplots()
formula = ("C$_2$H$_4$", "O$_2$", "CO", "H$_2$O", "CO$_2$")
[ax.plot(x, y[i]*1e+03, label=formula[i]) for i in range(5)]
ax.legend()
#ax.set_xlim([0, 100])
plt.xlabel(r"Time [$\mu$s]")
plt.ylabel("Concentration [mol/m$^3$]")
plt.title("Concentration over combustion")
plt.show()
#plt.savefig("images/concentration.pdf")
