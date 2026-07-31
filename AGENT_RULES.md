# Laboratory Asset Script Authoring Rules

The initial model and the GPT iteration agent may create or revise exactly one Blender Python instrument script.
The shared toolkit, reference implementation, documentation, and agent package are immutable context.

## Generated-script contract

The generated script must:

1. Import `workspace/toolkit/lab_blender_toolkit.py` as `lab` using a robust path derived from `__file__`.
2. Define `build_asset() -> bpy.types.Object` and call it under `if __name__ == "__main__":`.
3. Read the optional environment variables `LAB_ASSET_OUTPUT_DIR`, `LAB_RENDER_ENGINE`, and
   `LAB_RENDER_RESOLUTION`.
4. Clear and configure the scene; create separate asset, marking, and studio collections; configure camera and
   lighting; render at least three diagnostic views; and save a `.blend` file.
5. Save outputs only below `LAB_ASSET_OUTPUT_DIR` when it is set.
6. Add meaningful metadata to the main asset object.
7. Prefer toolkit functions. Add local helper geometry only when the toolkit lacks the required topology.
8. Keep physical dimensions explicit and plausible. Use real wall thickness, closed meshes, correct normals,
   and non-self-intersecting profiles.
9. Ensure the inner profile holds at least the nominal volume: `lab.profile_capacity_ml(inner_profile)` must be
   `>= nominal_volume_ml`. If it is not, enlarge the inner profile geometry rather than lowering the nominal
   volume to match a too-small profile.
10. Never use network access, subprocesses, shell commands, package installation, destructive filesystem
    operations, or dynamic execution such as `eval` and `exec`.
11. Fix geometry instead of hiding defects through camera, lighting, cropping, exposure, or background changes.

## Geometry-only visual evaluation policy

- Treat dark renders, weak reflections/highlights, low apparent transparency, dull glass, exposure, contrast,
  shadows, and other photometric differences as environment-lighting artifacts, not asset defects.
- Do not lower the score, create an issue, or revise the script solely for those appearance conditions.
- Do not change camera, lighting, world strength, exposure, background, or glass roughness merely to make a render
  prettier. Judge the geometry whenever it is readable in at least one supplied view.
- Focus review and revision on silhouette, dimensions/proportions, openings, wall thickness, rims, joints, side
  parts, topology, physical connections, and graduation geometry.

## Combined visual-review and revision policy

The GPT iteration agent sees the exact script that produced the supplied views. It must judge renders and code
together. When revision is required, it must return the complete corrected script in the same response rather
than a patch or advice for another model. It should trace visible defects to likely code parameters, preserve
already-correct geometry, and prioritize critical cross-view geometry problems over cosmetic rendering issues.

Every later review and render-error repair also receives the complete chronological history of all previous
moderate, major, and critical issues. Treat this history as regression memory: verify each old issue against
the current script/renders, preserve successful fixes, and avoid reintroducing previously reported defects. An old
issue is not proof that the defect still exists, so do not repeat it without current evidence.

## Graduation evaluation policy

Verify graduations from the code, not just by visual inspection. Ensure in the code that the graduations are
derived from volume integration. For vessels with non-uniform cross-sections, non-uniform spacing is normal
(equal-volume ticks are denser where the vessel is narrower). Focus on errors such as ticks detaching or
floating off the surface, overlapping labels, or missing marks. Never "fix" a correct volume-graduation
pattern by making the spacing uniform; that destroys volumetric accuracy.

For whole-millilitre capacities and intervals, prefer integer literals such as `250`, `25`, and `50` when
calling `add_volume_graduations`. The toolkit also accepts integer-valued floats such as `250.0`, but fractional
millilitre graduation intervals are not supported by this helper.
