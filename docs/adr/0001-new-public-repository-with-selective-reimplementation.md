# New Public Repository With Selective Reimplementation

CodeCairn is a new public repository. Pythia remains a private frozen prototype
and EverOS remains a mechanism-level reference.

The new repository is not a blank rewrite. It selectively ports verified seams
and regression cases from Pythia, rewrites invalid core contracts, and consults
EverOS for invariants rather than copying its architecture.

This keeps private traces and misleading historical reports out of public Git
history without discarding useful engineering evidence.
