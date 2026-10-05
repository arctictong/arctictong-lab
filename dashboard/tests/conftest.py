"""Nothing global here on purpose.

``pythonpath = ["src"]`` in pyproject.toml puts the package on the path, so these
tests import ``dashboard.*`` from a clean checkout with no editable install and
no PYTHONPATH.  A conftest that inserted the path would hide a packaging mistake
until the deploy, which is the worst time to find one.
"""
