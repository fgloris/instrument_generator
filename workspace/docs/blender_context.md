# Curated Blender 5.2 context for the authoring agent

This project runs Blender in background mode and executes a Python file. The instrument script is responsible for scene creation, render paths, and saving the `.blend` file.

## Project-specific facts

- The supplied toolkit explicitly requires Blender 5.2.
- Toolkit geometry is expressed in Blender metres; use `lab.mm(value)` for millimetres.
- `create_hollow_revolved_mesh` builds a closed hollow vessel from outer and inner vertical profiles and supports angular deformation such as a pouring spout.
- `add_volume_graduations` computes tick heights from the inner-volume profile, then wraps ticks and labels to the actual outer surface.
- `add_volume_graduations` accepts positive whole-millilitre values, including integer-valued floats such as
  `250.0`; use integer literals in generated scripts when the specification is a whole number.
- `configure_scene`, `create_grid_floor`, `create_camera`, `setup_glass_product_lighting`, `render_views`, and `save_blend` form the standard rendering pipeline.
- The reference beaker demonstrates the expected organization, but new instruments may require local helper functions for necks, side arms, handles, stoppers, joints, or non-axisymmetric parts.

## Headless execution constraints

- Do not depend on UI context, active editor areas, or manual mode changes.
- Prefer Blender data API and explicit object linking over context-sensitive operators.
- When operators are necessary, set selection and active object deterministically.
- Output paths must be absolute or derived from `Path(__file__)` / `LAB_ASSET_OUTPUT_DIR`.
- A successful script must produce multiple PNG views and a `.blend` file without opening the Blender GUI.

Place additional Blender HTML/text documentation in this directory. The code-writing model receives these files as immutable context.

## Required generated-script bootstrap

A generated file lives under `workspace/generated/`, while the immutable toolkit lives under
`workspace/toolkit/`. Use this pattern rather than assuming the current working directory:

```python
import importlib
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLKIT_DIR = SCRIPT_DIR.parent / "toolkit"
if str(TOOLKIT_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_DIR))

import lab_blender_toolkit as lab
importlib.reload(lab)

NAME = "Stable_Asset_Name"
OUTPUT_DIR = Path(
    os.getenv("LAB_ASSET_OUTPUT_DIR", str(SCRIPT_DIR / "output" / NAME))
).resolve()
RENDER_ENGINE = os.getenv("LAB_RENDER_ENGINE", "BLENDER_EEVEE")
RESOLUTION = int(os.getenv("LAB_RENDER_RESOLUTION", "768"))
```

Pass `RENDER_ENGINE` and `RESOLUTION` into `lab.configure_scene`, and pass `OUTPUT_DIR` to both
`lab.render_views` and `lab.save_blend`. Do not write files elsewhere when `LAB_ASSET_OUTPUT_DIR` is set.
