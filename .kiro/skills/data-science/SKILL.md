---
name: data-science
description: Data science - framing questions/hypotheses, EDA, statistical inference, modeling, honest evaluation, and reproducible analysis with quantified uncertainty. Use when analyzing data, designing experiments, or building/evaluating a model.
---

# Data Science

## Golden rules
- Start from a question, not a model. Know what a useful answer looks like before you fit anything.
- Inspect the data before you trust it. Distributions, missingness, outliers, and how it was collected.
- A simple, interpretable baseline first. Only add complexity that measurably earns its keep.
- Report uncertainty, not just a point estimate. And never claim causation from correlation.

## Workflow
1. **Frame**: precise question/hypothesis; define target, population, and success metric.
2. **Explore (EDA)**: profile features and target; find leakage risks, imbalance, and data-quality issues.
3. **Prepare**: clean, transform, and engineer features - fit transforms on TRAIN only.
4. **Split**: train/validation/test up front; cross-validate when the sample is small.
5. **Model**: baseline -> candidate models; tune on validation, never on test.
6. **Evaluate**: right metric, error analysis, calibration; compare against the baseline.
7. **Communicate**: answer + confidence first, then method and caveats; make it reproducible.

## Statistics done right
- State the hypothesis before looking. One pre-registered test beats fishing for p < 0.05.
- Report effect size AND uncertainty (confidence/credible intervals), not just significance.
- Check assumptions (normality, independence, homoscedasticity) before trusting a test.
- Mind multiple comparisons (correct for them) and base rates (Bayes, not just p-values).
- Correlation != causation. Name confounders; only claim causal effect with a valid design.

## Modeling & validation
- Prevent leakage: no target-derived features, no test-set peeking, no fitting scalers on all data.
- Watch bias/variance: a gap between train and validation score signals over/underfitting.
- Pick the metric for the problem: imbalance -> PR-AUC/F1 over accuracy; ranking -> NDCG; probabilities -> log-loss/calibration; regression -> MAE/RMSE with context.
- Validate on data that resembles production (time-based splits for temporal data).

## Reproducibility
- Fix random seeds; record the data version/snapshot and the exact steps.
- Keep analysis in versioned scripts/notebooks under the repo; results must be re-runnable.
- Log the environment (package versions) for anything that will be revisited.

## Definition of done
- The question is answered with a metric, an uncertainty range, and stated limitations.
- Validation is leakage-free and matches the deployment setting. Baseline comparison is shown.
- The analysis re-runs deterministically from recorded inputs.

## Anti-patterns
- Modeling before understanding the data. p-hacking / HARKing. Reporting accuracy on imbalanced data.
- Tuning on the test set. Overfitting a complex model when a baseline suffices.
- A bare point estimate with no uncertainty. Claiming causation from an observational correlation.

## Hand-offs
- Production training/serving, MLOps, fine-tuning -> alfred-ml.
- Data pipelines/ETL/warehousing -> alfred-data-engineer.
- Reporting/dashboards -> alfred-data. Deep proofs/derivations -> alfred-math.
