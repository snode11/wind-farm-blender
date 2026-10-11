"""Video-constrained NREL single-blade research experiment.

The input/decode/optimization modules only consume a signed allowlisted bundle.
Evaluation is a separate entry point and is the only reader of truth surfaces.
"""
