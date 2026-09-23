"""Vectorised SUMO XY → lon/lat conversion.

sumolib can convert one point at a time; streaming thousands of vehicles per
step needs numpy, so we build the pyproj transformer from the net's <location>.
"""

import re
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer


class NetProjection:
    def __init__(self, net_offset: tuple[float, float], proj_parameter: str):
        self.offset_x, self.offset_y = net_offset
        self._tf = Transformer.from_crs(
            CRS.from_proj4(proj_parameter), "EPSG:4326", always_xy=True
        )

    @classmethod
    def from_net_file(cls, net_file: Path) -> "NetProjection":
        # <location> is near the top of the file; avoid parsing the whole net.
        with open(net_file, encoding="utf-8") as f:
            head = f.read(20000)
        m = re.search(r'<location netOffset="([^"]+)"[^>]*projParameter="([^"]+)"', head)
        if not m:
            raise ValueError(f"No geo-referenced <location> in {net_file}")
        ox, oy = (float(v) for v in m.group(1).split(","))
        return cls((ox, oy), m.group(2))

    def to_lonlat(self, xs, ys) -> tuple[np.ndarray, np.ndarray]:
        xs = np.asarray(xs, dtype=np.float64) - self.offset_x
        ys = np.asarray(ys, dtype=np.float64) - self.offset_y
        return self._tf.transform(xs, ys)
