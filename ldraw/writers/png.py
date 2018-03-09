"""
png.py - A PNG writer for the ldraw Python package.

Copyright (C) 2010 David Boddie <david@boddie.org.uk>

This file is part of the ldraw Python package.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import numpy
from PyQt4.QtGui import QColor, QImage, qRed, qGreen, qBlue, qRgb

from ldraw.geometry import Vector, Vector2D
from ldraw.writers.common import Writer
from ldraw.writers.geometry import Z_MAX, Edge


class PNGArgs(object):
    def __init__(self, distance, image_size, stroke_colour=None, background_colour=None):
        """
        :param distance: distance of the camera
        :param image_size: size of the image as a string (e.g. '800x800')
        :param stroke_colour: colour of the edges
        :param background_colour: colour of the background
        """
        self.distance = distance
        self.image_size = image_size
        self.stroke_colour = stroke_colour
        self.background_colour = background_colour


class PNGWriter(Writer):
    """
    Renders a LDR model into a PNG
    """

    # pylint: disable=too-few-public-methods

    def __init__(self, camera_position, axes, parts):
        self.parts = parts
        self.lights = []
        self.minimum = Vector(0, 0, 0)
        self.maximum = Vector(0, 0, 0)
        self.bbox_cache = {}
        self.camera_position = camera_position
        self.axes = axes

    def write(self, model, png_path, png_args):
        """
        Writes the model's polygons to the provided PNG file

        :param model: LDR model
        :type model: Part
        :param png_path: where to output the PNG
        :param png_args: Arguments for the rendering (distance, etc.)
        :type png_args: PNGArgs

        :return:
        """
        distance = png_args.distance
        image_size = png_args.image_size
        stroke_colour = png_args.stroke_colour
        background_colour = png_args.background_colour
        image = QImage(image_size[0], image_size[1], QImage.Format_RGB16)
        depth = numpy.empty((image_size[0], image_size[1]), "f")
        depth[:] = 1 << 32 - 1
        polygons = self._polygons_from_objects(model)
        if stroke_colour:
            stroke_colour = QColor(stroke_colour).rgb()
        if background_colour is not None:
            image.fill(QColor(background_colour).rgb())
        viewport_scale = min(float(image_size[0]), float(image_size[1]))
        # Draw opaque polygons first.
        for polygon in polygons:
            if polygon.alpha == 1.0:
                polygon.project(distance)
                polygon.render(image, depth, viewport_scale, stroke_colour)
        # Draw translucent polygons last.
        for polygon in polygons:
            if polygon.alpha < 1.0:
                polygon.project(distance)
                polygon.render(image, depth, viewport_scale, stroke_colour)
        image.save(png_path)

    def _get_polygon(self, colour, projections):
        rgb = self.parts.colours.get(colour, "#ffffff")
        alpha = self._opacity_from_colour(colour)
        return [Polygon(projections, rgb, alpha)]


class Polygon(object):
    """
    Describes a polygon for PNG rendering
    """

    def __init__(self, points, rgb, alpha):
        self.points = points
        colour = QColor(rgb)
        self.red = colour.red()
        self.green = colour.green()
        self.blue = colour.blue()
        colour.setAlphaF(alpha)
        self.alpha = alpha
        self.rgba = colour.rgb()
        self.projected = []

    def project(self, distance):
        # px/c = x/(c + z)
        # px = c * x / (c + z)
        for point in self.points:
            self.projected.append(
                Vector((distance * point.x) / (distance + -point.z),
                 (distance * point.y) / (distance + -point.z), -point.z)
            )

    def render(self, image, depth, viewport_scale, stroke_colour):
        # Sort the edges of the polygon by their minimum projected y
        # coordinates, discarding horizontal edges.
        edges = self.get_edges(image, viewport_scale)
        if not edges:
            return
        width = image.width()
        height = image.height()

        end_py = edges[-1].t[1]
        if end_py < 0:
            return

        edge1 = edges.pop(0)

        if edge1.y1 >= height:
            return

        edge2 = edges.pop(0)

        int_edge1_y1 = int(edge1.y1)

        if int_edge1_y1 < edge1.y1 or int_edge1_y1 < edge2.y1:
            int_edge1_y1 += 1
        while int_edge1_y1 <= end_py and int_edge1_y1 < height:
            # Retrieve new edges as required.
            if int_edge1_y1 >= edge1.y2:
                if not edges:
                    break
                edge1 = edges.pop(0)
            if int_edge1_y1 >= edge2.y2:
                if not edges:
                    break
                edge2 = edges.pop(0)
            if int_edge1_y1 < 0:
                int_edge1_y1 += 1
                continue
            # Calculate the starting and finishing coordinates of the span
            # at the current y coordinate.
            gradient_1 = Vector2D(edge1.dx_dy, edge1.dz_dy)
            start_point_1 = Vector2D(edge1.x1, edge1.z1)
            start_1 = start_point_1 + (int_edge1_y1 - edge1.y1) * gradient_1

            gradient_2 = Vector2D(edge2.dx_dy, edge2.dz_dy)
            start_point_2 = Vector2D(edge2.x1, edge2.z1)
            start_2 = start_point_2 + (int_edge1_y1 - edge2.y1) * gradient_2

            # Do not render the span if it lies outside the image or has
            # values that cannot be stored in the depth buffer.
            # Truncate the span if it lies partially within the image.
            if start_1.x > start_2.x:
                start_2, start_1 = start_1, start_2
            # Only calculate a depth gradient for the span if it is more than
            # one pixel wide.
            if start_1.y <= 0 and start_2.y <= 0:
                int_edge1_y1 += 1
                continue
            elif start_1.y >= Z_MAX and start_2.y >= Z_MAX:
                int_edge1_y1 += 1
                continue

            start_x, end_sx = int(start_1.x), int(start_2.x)
            if start_x < start_1.x:
                start_x += 1
            if start_x >= width:
                int_edge1_y1 += 1
                continue
            elif end_sx < 0:
                int_edge1_y1 += 1
                continue

            int_edge1_y1 = self.draw_span(depth, end_sx, image, int_edge1_y1, start_1, start_2, start_x,
                                          stroke_colour, width)

    def get_edges(self, image, viewport_scale):
        width = image.width()
        height = image.height()
        edges = []
        len_points = len(self.points)
        for i in range(len_points):
            point1 = self.projected[i].copy()
            point1.x = width / 2 + (point1.x * viewport_scale)
            point1.y = height / 2 - (point1.y * viewport_scale)
            j = (i + 1) % len_points

            point2 = self.projected[j].copy()
            point2.x = width / 2 + (point2.x * viewport_scale)
            point2.y = height / 2 - (point2.y * viewport_scale)
            # Append the starting and finishing y coordinates, the starting
            # x coordinate, the dx/dy gradient of the edge, the starting
            # z coordinate and the dz/dy gradient of the edge.
            if int(point1.y) < int(point2.y):
                edges.append(Edge(point1, point2))
            elif int(point1.y) > int(point2.y):
                edges.append(Edge(point2, point1))
        edges.sort(key=lambda e: e.t)
        return edges

    def draw_span(self, depth, end_sx, image, int_edge1_y1, start_1, start_2, start_x, stroke_colour,
                  width):
        if start_1.x != start_2.x:
            start_dz_dx = (start_2.y - start_1.y) / (start_2.x - start_1.x)
        else:
            start_dz_dx = 0.0
        if start_x < 0:
            start_x = 0
        if end_sx >= width:
            end_sx = width - 1
        # Draw the span.
        while start_x <= end_sx:
            start_z = start_1.y + start_dz_dx * (start_x - start_1.x)
            if 0 < start_z <= depth[int(start_x)][int(int_edge1_y1)]:
                if self.alpha < 1.0:
                    pixel = image.pixel(start_x, int_edge1_y1)
                    dred = qRed(pixel)
                    dgreen = qGreen(pixel)
                    dblue = qBlue(pixel)
                    red = (1 - self.alpha) * dred + self.alpha * self.red
                    green = (1 - self.alpha) * dgreen + self.alpha * self.green
                    blue = (1 - self.alpha) * dblue + self.alpha * self.blue
                    image.setPixel(start_x, int_edge1_y1, qRgb(red, green, blue))
                else:
                    depth[int(start_x)][int(int_edge1_y1)] = start_z
                    image.setPixel(start_x, int_edge1_y1, self.rgba)
            start_x += 1
        if stroke_colour:
            if 0 <= start_1.x < width and 0 < start_1.y <= depth[int(start_1.x)][int(int_edge1_y1)]:
                image.setPixel(start_1.x, int_edge1_y1, stroke_colour)
            if 0 <= start_2.x < width and 0 < start_2.y <= depth[int(start_2.x)][int(int_edge1_y1)]:
                image.setPixel(start_2.x, int_edge1_y1, stroke_colour)
        int_edge1_y1 += 1
        return int_edge1_y1