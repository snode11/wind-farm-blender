# Landscape asset sources

- Rocky Terrain 02, Poly Haven: https://polyhaven.com/a/rocky_terrain_02
  2K JPG diffuse and roughness maps; roughness also drives the bump height.
  The packaged reconstruction example retains its embedded OpenGL normal map;
  it does not require a separate normal-map file in this directory.
- Kloofendal 48d Partly Cloudy (Pure Sky), Poly Haven:
  https://polyhaven.com/a/kloofendal_48d_partly_cloudy_puresky
  2K HDR sky.

Downloaded using the Poly Haven files API. Downloads verified against its MD5
metadata. Assets are provided by Poly Haven under CC0; see https://polyhaven.com/license.
The render script packs images into the saved blend file. Realtime Demo uses
these bundled files offline; no download is performed by the extension.
