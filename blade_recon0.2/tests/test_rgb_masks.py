import importlib.util
from pathlib import Path
import sys
import numpy as np
import unittest
p=Path(__file__).resolve().parents[1]/'rgb_masks.py'
spec=importlib.util.spec_from_file_location('rgb_masks',p);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def scene():
    bg=np.zeros((96,128,3),np.uint8);bg[:]=(80,140,220)
    bg[:,60:68]=(170,170,170) # static grey tower shares blade colour family
    observed=bg.copy();observed[32:56,18:112]=(190,190,190)
    observed[40:47,43:56]=(40,40,40) # bounded dark repair appearance
    observed[32:56,106:112]=(220,15,15) # real RGB red tip
    return observed,bg

class RGBMaskTests(unittest.TestCase):
    def test_independent_background_preserves_persistent_root_and_paint(self):
        rgb,bg=scene();mask,e=m.background_instance_mask(rgb,bg)
        assert mask[44,63] == 255 # observed persistent blade in front of grey tower
        assert mask[43,48] == 255 # bounded paint doesn't make a false contour
        assert mask[42,108] == 255 # red tip survives
        assert mask[10,63] == 0 # static tower is not target occupancy
        assert e['rgb_only']

    def test_static_gray_cloud_cannot_be_selected_by_material_alone(self):
        rgb,bg=scene();rgb[3:20,5:50]=(200,200,200);bg[3:20,5:50]=(200,200,200)
        mask,_=m.background_instance_mask(rgb,bg)
        assert not mask[3:20,5:50].any()

    def test_identical_foreground_and_background_are_not_fabricated(self):
        bg=np.full((64,64,3),170,np.uint8)
        mask,_=m.background_instance_mask(bg.copy(),bg)
        assert not mask.any() # no RGB evidence; no geometry-based completion

    def test_independent_reference_noise_rejects_static_changes(self):
        bg=np.full((96,128,3),120,np.uint8)
        repeat=np.full_like(bg,140)
        rgb=bg.copy();rgb[15:40,10:40]=135;rgb[50:80,60:110]=220
        raw,_=m.background_instance_mask(rgb,bg)
        calibrated,e=m.background_instance_mask(rgb,bg,reference_repeat_rgb=repeat)
        assert raw[25,25] == 255
        assert calibrated[25,25] == 0
        assert calibrated[65,80] == 255
        assert e['reference_noise_calibrated']

    def test_reference_and_original_pixel_contract_are_strict(self):
        rgb,bg=scene()
        with self.assertRaises(ValueError):m.background_instance_mask(rgb,bg[:-1])
        with self.assertRaises(ValueError):m.background_instance_mask(rgb.astype(np.float32),bg)

    def test_outside_pixels_are_never_inferred_from_holes_or_geometry(self):
        rgb,bg=scene();rgb[32:56,:18]=bg[32:56,:18]
        mask,_=m.background_instance_mask(rgb,bg)
        assert not mask[32:56,:16].any()

    def test_diagonal_background_channel_remains_connected_to_image_edge(self):
        bg=np.full((15,15,3),(80,140,220),np.uint8)
        foreground=np.zeros((15,15),bool)
        foreground[2:13,2:13]=True
        foreground[5:10,5:10]=False
        # Eight-connected exterior background reaches the central gap. It is
        # not a bounded paint hole, even though an external contour encloses it.
        for pixel in range(2,6):foreground[pixel,pixel]=False
        rgb=bg.copy();rgb[foreground]=(190,190,190)
        config=m.BackgroundMaskConfig(rgb_sigma_px=.01,close_kernel_px=1,
            component_area_min_px=1)
        mask,_=m.background_instance_mask(rgb,bg,config)
        self.assertTrue(np.array_equal(mask>127,foreground))

    def test_truly_bounded_hole_still_preserves_interior_paint(self):
        bg=np.full((15,15,3),(80,140,220),np.uint8)
        rgb=bg.copy();rgb[2:13,2:13]=(190,190,190)
        rgb[5:10,5:10]=(40,40,40)
        config=m.BackgroundMaskConfig(rgb_sigma_px=.01,close_kernel_px=1,
            component_area_min_px=1)
        mask,_=m.background_instance_mask(rgb,bg,config)
        self.assertTrue((mask[2:13,2:13]==255).all())
        self.assertFalse(mask[:2].any())

if __name__ == "__main__": unittest.main()
