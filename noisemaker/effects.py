from enum import Enum

import random

import numpy as np
import tensorflow as tf

from skimage.transform import resize
from skimage.util import crop, pad


class ConvKernel(Enum):
    """ A collection of convolution kernels for image post-processing, based on well-known recipes. """

    emboss = [
        [   0,   2,   4   ],
        [  -2,   1,   2   ],
        [  -4,  -2,   0   ]
    ]

    shadow = [
        # yeah, one of the really big fuckers
        [  0,   1,   1,   1,   1,   1,   1  ],
        [ -1,   0,   2,   2,   1,   1,   1  ],
        [ -1,  -2,   0,   4,   2,   1,   1  ],
        [ -1,  -2,  -4,   8,   4,   2,   1  ],
        [ -1,  -1,  -2,  -4,   0,   2,   1  ],
        [ -1,  -1,  -1,  -2,  -2,   0,   1  ],
        [ -1,  -1,  -1,  -1,  -1,  -1,   0  ]

        # [  0,  1,  1,  1, 0 ],
        # [ -1, -2,  4,  2, 1 ],
        # [ -1, -4,  2,  4, 1 ],
        # [ -1, -2, -4,  2, 1 ],
        # [  0, -1, -1, -1, 0 ]

        # [  0,  1,  1,  1, 0 ],
        # [ -1, -2,  4,  2, 1 ],
        # [ -1, -4,  2,  4, 1 ],
        # [ -1, -2, -4,  2, 1 ],
        # [  0, -1, -1, -1, 0 ]
    ]

    edges = [
        [   1,   2,  1   ],
        [   2, -12,  2   ],
        [   1,   2,  1   ]
    ]

    sharpen = [
        [   0, -1,  0 ],
        [  -1,  5, -1 ],
        [   0, -1,  0 ]
    ]

    unsharp_mask = [
        [ 1,  4,     6,   4, 1 ],
        [ 4,  16,   24,  16, 4 ],
        [ 6,  24, -476,  24, 6 ],
        [ 4,  16,   24,  16, 4 ],
        [ 1,  4,     6,   4, 1 ]
    ]


def _conform_kernel_to_tensor(kernel, tensor):
    """ Re-shape a convolution kernel to match the given tensor's color dimensions. """

    l = len(kernel)

    channels = tf.shape(tensor).eval()[2]

    temp = np.repeat(kernel, channels)

    temp = tf.reshape(temp, (l, l, channels, 1))

    temp = tf.image.convert_image_dtype(temp, tf.float32, saturate=True)

    return temp


def convolve(kernel, tensor):
    """
    Apply a convolution kernel to an image tensor.

    :param ConvKernel kernel: See ConvKernel enum
    :param Tensor tensor: An image tensor.
    :return: Tensor
    """

    height, width, channels = tf.shape(tensor).eval()

    kernel = _conform_kernel_to_tensor(kernel.value, tensor)

    # Give the conv kernel some room to play on the edges
    pad_height = int(height * .25)
    pad_width = int(width * .25)
    padding = ((pad_height, pad_height), (pad_width, pad_width), (0, 0))
    tensor = tf.stack(pad(tensor.eval(), padding, "wrap"))

    tensor = tf.nn.depthwise_conv2d([tensor], kernel, [1,1,1,1], "VALID")[0]

    # Playtime... is... over!
    post_height, post_width, channels = tf.shape(tensor).eval()
    crop_height = int((post_height - height) * .5)
    crop_width = int((post_width - width) * .5)
    tensor = crop(tensor.eval(), ((crop_height, crop_height), (crop_width, crop_width), (0, 0)))

    tensor = normalize(tensor)

    return tensor


def normalize(tensor):
    """
    Squeeze the given Tensor into a range between 0 and 1.

    :param Tensor tensor: An image tensor.
    :return: Tensor
    """

    return tf.divide(
        tf.subtract(tensor, tf.reduce_min(tensor)),
        tf.subtract(tf.reduce_max(tensor), tf.reduce_min(tensor))
    )


def resample(tensor, width, height, spline_order=3):
    """
    Resize the given image Tensor to the given dimensions.

    :param Tensor tensor: An image tensor.
    :param int width: Output width.
    :param int height: Output height.
    :param int spline_order: Spline point count. 0=Constant, 1=Linear, 3=Bicubic, others may not work.
    :return: Tensor
    """

    _height, _width, channels = tf.shape(tensor).eval()

    if isinstance(tensor, tf.Tensor):  # Sometimes you feel like a Tensor
        downcast = tensor.eval()

    else:  # Sometimes you feel a little more numpy
        downcast = tensor

    downcast = resize(downcast, (height, width, channels), mode="wrap", order=spline_order, preserve_range=True)

    return tf.image.convert_image_dtype(downcast, tf.float32, saturate=True)

    ### TensorFlow doesn't handily let us wrap around edges when resampling.
    # temp = tf.image.resize_images(tensor, [height, width], align_corners=True, method=tf.image.ResizeMethod.BICUBIC)
    # temp = tf.image.convert_image_dtype(temp, tf.float32, saturate=True)
    # return temp


def crease(tensor):
    """
    Create a "crease" (ridge) at midpoint values. (1 - unsigned((n-.5)*2))

    :param Tensor tensor: An image tensor.
    :return: Tensor
    """

    temp = tf.subtract(tensor, .5)
    temp = tf.multiply(temp, 2)
    temp = tf.maximum(temp, temp*-1)

    temp = tf.subtract(tf.ones(tf.shape(temp)), temp)

    return temp


def displace(tensor, displacement=1.0):
    """
    Apply self-displacement along X and Y axes, based on each pixel value.

    Current implementation is slow.

    :param Tensor tensor: An image tensor.
    :param float displacement:
    :return: Tensor
    """

    shape = tf.shape(tensor).eval()

    height, width, channels = shape

    reference = tf.image.rgb_to_grayscale(tensor) if channels > 2 else tensor

    reference = tf.subtract(reference, .5)
    reference = tf.multiply(reference, 2 * displacement)

    reference = reference.eval()
    tensor = tensor.eval()

    temp = np.zeros(shape)

    base_x_offset = int(random.random() * width)
    base_y_offset = int(random.random() * height)

    # I know this can be done much faster with Tensor indexing. Working on it.
    for x in range(width):
        for y in range(height):
            x_offset = (x + int(reference[(y + base_y_offset) % height][x] * width)) % width
            y_offset = (y + int(reference[y][(x + base_x_offset) % width] * height)) % height

            temp[y][x] = tensor[y_offset][x_offset]

    temp = tf.image.convert_image_dtype(temp, tf.float32, saturate=True)

    return temp


def wavelet(tensor):
    """
    Convert regular noise into 2-D wavelet noise.

    Completely useless. Maybe useful if Noisemaker supports higher dimensions later.

    :param Tensor tensor: An image tensor.
    :return: Tensor
    """

    shape = tf.shape(tensor).eval()

    height, width, channels = shape

    return tensor - resample(resample(tensor, int(width * .5), int(height * .5)), width, height)
