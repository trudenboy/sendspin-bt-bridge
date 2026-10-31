"""The physical layer as a read model: controllers, bonds, link roles.

Everything here answers questions about hardware the bridge did not create and
cannot reconfigure from this surface.  One reader (:mod:`inventory`) is the
only place that talks to BlueZ; the rest is pure.
"""
