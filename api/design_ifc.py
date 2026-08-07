"""
api/design_ifc.py — Author a real IFC4 file from a Design Studio spec.

Turns the in-app design (v1 parametric or v2 plan-based) into a valid IFC4
model with a proper spatial hierarchy (Project → Site → Building → Storeys) and
one tessellated element per generated mesh (IfcWall / IfcSlab / IfcColumn /
IfcWindow / IfcDoor / IfcRoof / IfcFooting). The result imports into Revit,
ArchiCAD, Blender/Bonsai, etc. — and straight back into this app's own IFC
pipeline.

Requires: ifcopenshell (already a dependency for IFC upload).
"""

from __future__ import annotations

from api.design_generator import generate, normalize, is_v2


def export_ifc(spec: dict, out_path: str, name: str = "Building Design") -> dict:
    """Generate an IFC4 file at out_path. Returns {element_count, storey_count}."""
    import ifcopenshell
    import ifcopenshell.guid as guid

    s = normalize(spec)
    geo = generate(s, None)
    meshes = [m for m in geo.get("meshes", []) if m.get("vertices") and m.get("indices")]

    f = ifcopenshell.file(schema="IFC4")

    def new_guid() -> str:
        return guid.new()

    origin = f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    dir_z = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
    dir_x = f.create_entity("IfcDirection", DirectionRatios=(1.0, 0.0, 0.0))
    axis = f.create_entity(
        "IfcAxis2Placement3D", Location=origin, Axis=dir_z, RefDirection=dir_x
    )
    ctx = f.create_entity(
        "IfcGeometricRepresentationContext",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-5,
        WorldCoordinateSystem=axis,
        TrueNorth=None,
    )
    body_ctx = f.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Body",
        ContextType="Model",
        ParentContext=ctx,
        TargetView="MODEL_VIEW",
    )

    length_unit = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    units = f.create_entity("IfcUnitAssignment", Units=[length_unit])
    project = f.create_entity(
        "IfcProject",
        GlobalId=new_guid(),
        Name=name,
        RepresentationContexts=[ctx],
        UnitsInContext=units,
    )

    def placement(rel=None):
        return f.create_entity(
            "IfcLocalPlacement", PlacementRelTo=rel, RelativePlacement=axis
        )

    site_pl = placement(None)
    site = f.create_entity(
        "IfcSite", GlobalId=new_guid(), Name="Site",
        ObjectPlacement=site_pl, CompositionType="ELEMENT",
    )
    bld_pl = placement(site_pl)
    building = f.create_entity(
        "IfcBuilding", GlobalId=new_guid(), Name="Building",
        ObjectPlacement=bld_pl, CompositionType="ELEMENT",
    )

    # Storey elevations + names.
    storey_names = geo.get("storey_names") or []
    elevations: dict[int, float] = {}
    if is_v2(s):
        e = 0.0
        for i, st in enumerate(s["storeys"]):
            elevations[i] = e
            e += st["height"]

    present = sorted({m.get("storey", 0) for m in meshes if (m.get("storey", 0) or 0) >= 0})
    if not present:
        present = [0]

    storey_entities: dict[int, object] = {}
    storey_placements: dict[int, object] = {}
    for idx in present:
        spl = placement(bld_pl)
        name_i = storey_names[idx] if idx < len(storey_names) else f"Level {idx + 1}"
        st_ent = f.create_entity(
            "IfcBuildingStorey",
            GlobalId=new_guid(),
            Name=name_i,
            ObjectPlacement=spl,
            CompositionType="ELEMENT",
            Elevation=float(elevations.get(idx, 0.0)),
        )
        storey_entities[idx] = st_ent
        storey_placements[idx] = spl

    f.create_entity("IfcRelAggregates", GlobalId=new_guid(), RelatingObject=project, RelatedObjects=[site])
    f.create_entity("IfcRelAggregates", GlobalId=new_guid(), RelatingObject=site, RelatedObjects=[building])
    if storey_entities:
        f.create_entity(
            "IfcRelAggregates", GlobalId=new_guid(),
            RelatingObject=building, RelatedObjects=list(storey_entities.values()),
        )

    def make_product_shape(mesh):
        verts = mesh["vertices"]
        coords = [
            (float(verts[i]), float(verts[i + 1]), float(verts[i + 2]))
            for i in range(0, len(verts), 3)
        ]
        point_list = f.create_entity("IfcCartesianPointList3D", CoordList=coords)
        tri = mesh["indices"]
        coord_index = [
            (tri[i] + 1, tri[i + 1] + 1, tri[i + 2] + 1)
            for i in range(0, len(tri), 3)
        ]
        faceset = f.create_entity(
            "IfcTriangulatedFaceSet",
            Coordinates=point_list,
            Closed=False,
            CoordIndex=coord_index,
        )
        rep = f.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=body_ctx,
            RepresentationIdentifier="Body",
            RepresentationType="Tessellation",
            Items=[faceset],
        )
        return f.create_entity("IfcProductDefinitionShape", Representations=[rep])

    _VALID = {
        "IfcWall", "IfcSlab", "IfcColumn", "IfcWindow", "IfcDoor",
        "IfcRoof", "IfcFooting", "IfcBeam", "IfcCovering",
        "IfcDuctSegment", "IfcPipeSegment",
    }

    contained: dict[int, list] = {}
    foundation_elements: list = []
    element_count = 0

    for mesh in meshes:
        cls = mesh.get("ifc_type") or "IfcBuildingElementProxy"
        if cls not in _VALID:
            cls = "IfcBuildingElementProxy"
        st_idx = mesh.get("storey", 0)
        if st_idx is None:
            st_idx = 0
        rel_pl = storey_placements.get(st_idx, bld_pl)
        ent = f.create_entity(
            cls,
            GlobalId=new_guid(),
            Name=cls.replace("Ifc", ""),
            ObjectPlacement=placement(rel_pl),
            Representation=make_product_shape(mesh),
        )
        element_count += 1
        if st_idx < 0 or st_idx not in storey_entities:
            foundation_elements.append(ent)
        else:
            contained.setdefault(st_idx, []).append(ent)

    for idx, ents in contained.items():
        f.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=new_guid(),
            RelatingStructure=storey_entities[idx],
            RelatedElements=ents,
        )
    if foundation_elements:
        f.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=new_guid(),
            RelatingStructure=building,
            RelatedElements=foundation_elements,
        )

    # Rooms -> IfcSpace (extruded floor area), aggregated under their storey.
    space_count = 0
    if is_v2(s):
        z_dir = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
        for idx, st_ent in storey_entities.items():
            if idx >= len(s["storeys"]):
                continue
            rooms = s["storeys"][idx].get("rooms", [])
            if not rooms:
                continue
            elev = float(elevations.get(idx, 0.0))
            height = float(s["storeys"][idx].get("height", 3.0))
            spaces = []
            for rm in rooms:
                poly = rm.get("poly", [])
                if len(poly) < 3:
                    continue
                # Ensure counter-clockwise winding for a valid extruded profile.
                area2 = 0.0
                for i in range(len(poly)):
                    x1, y1 = poly[i]
                    x2, y2 = poly[(i + 1) % len(poly)]
                    area2 += x1 * y2 - x2 * y1
                ring = poly if area2 >= 0 else list(reversed(poly))
                pts = [f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y))) for x, y in ring]
                pts.append(pts[0])
                curve = f.create_entity("IfcPolyline", Points=pts)
                profile = f.create_entity(
                    "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=curve
                )
                pos = f.create_entity(
                    "IfcAxis2Placement3D",
                    Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, elev)),
                )
                solid = f.create_entity(
                    "IfcExtrudedAreaSolid",
                    SweptArea=profile,
                    Position=pos,
                    ExtrudedDirection=z_dir,
                    Depth=max(0.1, height),
                )
                rep = f.create_entity(
                    "IfcShapeRepresentation",
                    ContextOfItems=body_ctx,
                    RepresentationIdentifier="Body",
                    RepresentationType="SweptSolid",
                    Items=[solid],
                )
                prod = f.create_entity("IfcProductDefinitionShape", Representations=[rep])
                space = f.create_entity(
                    "IfcSpace",
                    GlobalId=new_guid(),
                    Name=rm.get("name", "Room"),
                    LongName=rm.get("name", "Room"),
                    ObjectType=(rm.get("type") or None),
                    ObjectPlacement=placement(storey_placements.get(idx, bld_pl)),
                    Representation=prod,
                    CompositionType="ELEMENT",
                )
                spaces.append(space)
                space_count += 1
            if spaces:
                f.create_entity(
                    "IfcRelAggregates", GlobalId=new_guid(),
                    RelatingObject=st_ent, RelatedObjects=spaces,
                )

    f.write(out_path)
    return {
        "element_count": element_count,
        "storey_count": len(storey_entities),
        "space_count": space_count,
    }
