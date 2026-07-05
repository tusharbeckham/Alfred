---
name: mathematics
description: Applied and pure mathematics for scientific ML — algebra, calculus, linear algebra, probability/stats, numerical methods, and proofs. Use when solving, deriving, implementing, or verifying mathematical results.
---

# Mathematics

## Golden rules
- State the problem precisely before solving. Ambiguous notation causes wrong answers.
- Show every non-trivial step. A skipped step is an unverified step.
- Verify independently: substitute back, check edge cases, cross-check with known identities.
- Distinguish exact results from approximations; state error bounds where relevant.

## Algebra and calculus
- Simplify symbolically before going numerical.
- Multivariate chain rule, Jacobians, and Hessians: derive explicitly for ML gradient work.
- Taylor/Maclaurin series: use for local approximations and convergence analysis.
- Integral transforms (Fourier, Laplace): identify the transform pair; state conditions.

## Linear algebra (ML-critical)
- Always check: dimensions, rank, positive-definiteness, conditioning (κ = σ_max/σ_min).
- Prefer SVD for rank-revealing decompositions; use Cholesky only when PD is confirmed.
- Eigendecomposition: state whether the matrix is symmetric; use `np.linalg.eigh` for
  symmetric, `eig` otherwise.
- Numerical: use `scipy.linalg` over `np.linalg` for stability; never invert a matrix
  directly — solve the linear system instead.

## Probability and statistics
- State the sample space, σ-algebra, and measure before deriving expectations.
- Distinguish frequentist and Bayesian interpretations; be explicit about which is used.
- Concentration inequalities (Hoeffding, Chebyshev, Chernoff): cite the bound name and
  state the required assumptions.
- For ML: derive bias-variance decomposition, information-theoretic quantities (KL, MI,
  entropy) from first principles when non-obvious.

## Numerical methods
- Check stability before choosing a method: explicit Euler is O(Δt) but conditionally
  stable; implicit methods cost more but are unconditionally stable.
- Finite differences: state order of accuracy and stencil used.
- Root-finding: Newton's method needs a good initial guess and a non-zero derivative;
  bisection is slower but always converges on a bracket.
- Quadrature: use Gaussian for smooth integrands; adaptive (scipy.integrate.quad) for
  singularities.
- Floating-point: avoid catastrophic cancellation; use Kahan summation for long sums;
  test with multiple precisions when in doubt.

## Proofs
- State strategy upfront (induction, contradiction, construction, direct, contrapositive).
- Base case + inductive step for induction. Explicitly reach the contradiction.
- Mark QED or □. Mark open gaps clearly with "UNVERIFIED" — never hide them.

## Implementation (Python/NumPy/SciPy)
```python
# Good: solve Ax = b without inverting
x = np.linalg.solve(A, b)           # not np.linalg.inv(A) @ b

# Good: stable eigendecomposition of symmetric matrix
vals, vecs = np.linalg.eigh(A)      # not eig for symmetric

# Good: condition number check before trusting a solve
kappa = np.linalg.cond(A)
if kappa > 1e10:
    warn(f"Ill-conditioned system (κ={kappa:.2e}); result may be inaccurate.")
```

## Anti-patterns
- Skipping dimension/unit checks. Inverting matrices directly. Ignoring conditioning.
- Claiming a proof is complete when it contains an unverified step.
- Using float32 for accumulation where float64 precision is needed.
- Fabricating theorem names or citations.