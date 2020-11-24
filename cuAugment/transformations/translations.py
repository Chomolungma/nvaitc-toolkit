# The MIT License (MIT)

# Copyright (c) 2020 NVIDIA CORPORATION.

# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from cuAugment.transformations.base import SpatialTransformation

__all__ = ['TranslateX', 
           'TranslateY', 
           'TranslateZ', 
           'TranslateT']

class TranslateX(SpatialTransformation):
    
    def __init__(self, distribution):
        super().__init__(distribution)
    
    def inline(self):
        '''translate x'''
        
        return ['x -= {param}[0]']
    
    def shape_params(self):
        return [1]
    
    def min_dims(self):
        return 1

class TranslateY(SpatialTransformation):
    
    def __init__(self, distribution):
        super().__init__(distribution)
    
    def inline(self):
        '''translate y'''
        
        return ['y -= {param}[0]']
    
    def shape_params(self):
        return [1]
    
    def min_dims(self):
        return 2
    
class TranslateZ(SpatialTransformation):
    
    def __init__(self, distribution):
        super().__init__(distribution)
    
    def inline(self):
        '''translate z'''
        
        return ['z -= {param}[0]']
    
    def shape_params(self):
        return [1]
    
    def min_dims(self):
        return 3

class TranslateT(SpatialTransformation):
    
    def __init__(self, distribution):
        super().__init__(distribution)
    
    def inline(self):
        '''translate t'''
        
        return ['t -= {param}[0]']
    
    def shape_params(self):
        return [1]
    
    def min_dims(self):
        return 4