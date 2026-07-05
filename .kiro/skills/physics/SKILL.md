---
name: physics
description: Classical and modern physics for scientific ML — mechanics, E&M, quantum, thermo, computational physics, and simulation. Use when solving physics problems, building simulations, or verifying physical models.
---

# Physics

## Golden rules
- State the physical model and approximations before solving. Wrong model = wrong answer.
- Carry units through every step. Dimensional inconsistency means something is wrong — stop.
- Verify via limiting cases, conservation laws, symmetry, or independent computation.
- Distinguish exact solutions from numerical approximations; state validity ranges.

## Classical mechanics
- Lagrangian/Hamiltonian preferred over F=ma for constrained or multi-body systems.
- State generalized coordinates, constraints (holonomic/non-holonomic), and symmetries.
- Conservation laws from Noether's theorem: time-translation→energy, space→momentum,
  rotation→angular momentum. Use them as cross-checks.
- Numerical integration: symplectic integrators (Verlet, leapfrog) for Hamiltonian
  systems — they conserve phase-space volume. Never use plain Euler for long-time dynamics.

## Electrodynamics
- Maxwell's equations in differential and integral form. Know when to use which.
- Boundary conditions: tangential E continuous, normal D jumps by σ_f, etc.
- Gauge choices: Coulomb for electrostatics, Lorenz for radiation. Be explicit.
- Units: SI by default. State if using Gaussian/CGS. Convert carefully.

## Quantum mechanics
- State the Hilbert space, basis, and Hamiltonian before solving.
- Distinguish pure states from mixed states (density matrix when needed).
- Perturbation theory: state the small parameter and order. Check convergence.
- Numerical: diagonalize H for small systems; use split-operator or Crank-Nicolson for
  time-dependent Schrödinger.
- Commutator algebra: verify [A,B] before claiming observables are compatible.

## Thermodynamics and statistical mechanics
- State the ensemble (microcanonical, canonical, grand canonical) and justify the choice.
- Identify the thermodynamic potential (F, G, Ω) natural to the constraints.
- Phase transitions: identify the order parameter, symmetry breaking, and universality class.
- Numerical stat mech: Monte Carlo (Metropolis-Hastings); check detailed balance and
  autocorrelation time.

## Computational physics
- PDE solvers: finite difference, finite element, spectral methods — match to problem
  geometry and boundary conditions.
- Stability: CFL condition for explicit hyperbolic solvers (Δt ≤ Δx/c).
- Validation: compare against exact solutions, manufactured solutions, or known benchmarks.
- Floating point: same rules as mathematics skill (condition numbers, catastrophic
  cancellation, precision).

## Scientific ML applications
- Physics-informed neural networks (PINNs): embed PDE residuals in the loss.
- Neural ODEs: use adjoint method for memory-efficient gradients.
- Symmetry-preserving architectures: equivariant networks for rotation/translation
  invariance. State what symmetry is being exploited.
- Always compare ML-based solutions against known physics baselines.

## Implementation (Python/NumPy/SciPy)
```python
# Good: symplectic Verlet integrator
def verlet_step(x, v, a_func, dt):
    v_half = v + 0.5 * dt * a_func(x)
    x_new = x + dt * v_half
    v_new = v_half + 0.5 * dt * a_func(x_new)
    return x_new, v_new

# Good: check energy conservation as validation
E_initial = kinetic(v[0]) + potential(x[0])
E_final = kinetic(v[-1]) + potential(x[-1])
assert abs(E_final - E_initial) / abs(E_initial) < 1e-6, "Energy drift too large"

# Good: CFL check before running explicit PDE solver
cfl = c * dt / dx
if cfl > 1.0:
    raise ValueError(f"CFL={cfl:.3f} > 1: reduce dt or increase dx")
```

## Anti-patterns
- Dropping units mid-derivation. Ignoring dimensional analysis.
- Using non-symplectic integrators for Hamiltonian systems (energy drift).
- Claiming a simulation is "validated" without comparing to a known result.
- Mixing SI and CGS without explicit conversion. Confusing ε₀ conventions.
- Ignoring validity ranges of approximations (e.g., using small-angle beyond 30°).
