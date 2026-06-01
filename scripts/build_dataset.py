from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import shapefile
from pyproj import Transformer


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DATA = ROOT / "public" / "data"
OUTPUT_FILES = [
    PUBLIC_DATA / "metadata.json",
    PUBLIC_DATA / "oa.geojson",
    PUBLIC_DATA / "parcels.geojson",
    PUBLIC_DATA / "plans.geojson",
]

PARCEL_ZIP = ROOT / "AL_D002_11_20260508.zip"
OA_ZIP = ROOT / "bnd_oa_11240_2025_2Q.zip"
PLAN_ZIPS = sorted(ROOT.glob("CH_D*_20260528.zip"))
DISTRICT_PLAN_ZIP = ROOT / "C_UQ161.zip"
POP_ZIP = ROOT / "_census_reqdoc_1780054203703.zip"
HOUSEHOLD_ZIP = ROOT / "_census_reqdoc_1780055403382.zip"


TRANSFORMERS = {
    "EPSG:5186": Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True),
    "EPSG:5179": Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True),
    "EPSG:5174": Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True),
}

PLAN_COLORS = [
    "#fde725",
    "#7ad151",
    "#22a884",
    "#2a788e",
    "#414487",
    "#440154",
    "#f9844a",
    "#4d908e",
    "#277da1",
    "#f15bb5",
    "#fee440",
    "#9b5de5",
]


def read_zip_shapefile(zip_path: Path, encoding: str = "cp949") -> shapefile.Reader:
    with zipfile.ZipFile(zip_path) as zf:
        payloads: dict[str, bytes] = {}
        for name in zf.namelist():
            suffix = Path(name).suffix.lower()
            if suffix in {".shp", ".shx", ".dbf"}:
                payloads[suffix] = zf.read(name)
    return shapefile.Reader(
        shp=io.BytesIO(payloads[".shp"]),
        shx=io.BytesIO(payloads[".shx"]),
        dbf=io.BytesIO(payloads[".dbf"]),
        encoding=encoding,
    )


def fields_map(reader: shapefile.Reader) -> list[str]:
    return [field[0] for field in reader.fields[1:]]


def ring_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for idx in range(len(points)):
        x1, y1 = points[idx]
        x2, y2 = points[(idx + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def transform_points(points: list[tuple[float, float]], epsg: str) -> list[list[float]]:
    transformer = TRANSFORMERS[epsg]
    coords = []
    for x, y in points:
        lon, lat = transformer.transform(x, y)
        coords.append([round(lon, 6), round(lat, 6)])
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def shape_to_geojson(shape: shapefile.Shape, epsg: str) -> dict:
    parts = list(shape.parts) + [len(shape.points)]
    rings = []
    for start, end in zip(parts[:-1], parts[1:]):
        ring = transform_points(shape.points[start:end], epsg)
        if len(ring) >= 4:
            rings.append(ring)

    outers: list[list[list[float]]] = []
    holes_by_outer: list[list[list[list[float]]]] = []
    for ring in rings:
        if ring_area([(x, y) for x, y in ring[:-1]]) < 0:
            outers.append(ring)
            holes_by_outer.append([])
        elif holes_by_outer:
            holes_by_outer[-1].append(ring)
        else:
            outers.append(ring)
            holes_by_outer.append([])

    polygons = []
    for outer, holes in zip(outers, holes_by_outer):
        polygons.append([outer, *holes])

    if not polygons:
        return {}
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def bbox_center(shape: shapefile.Shape, epsg: str) -> tuple[float, float]:
    xmin, ymin, xmax, ymax = shape.bbox
    transformer = TRANSFORMERS[epsg]
    lon, lat = transformer.transform((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
    return lon, lat


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        intersects = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
        )
        if intersects:
            inside = not inside
    return inside


def point_in_geom(point: tuple[float, float], geometry: dict) -> bool:
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
        if not point_in_ring(point, rings[0]):
            return False
        for hole in rings[1:]:
            if point_in_ring(point, hole):
                return False
        return True

    for polygon in geometry["coordinates"]:
        if not point_in_ring(point, polygon[0]):
            continue
        if any(point_in_ring(point, hole) for hole in polygon[1:]):
            continue
        return True
    return False


def geom_bounds(geometry: dict) -> tuple[float, float, float, float]:
    if not geometry:
        raise ValueError("empty geometry")
    xs: list[float] = []
    ys: list[float] = []

    def collect(rings: list[list[list[float]]]) -> None:
        for ring in rings:
            for lon, lat in ring:
                xs.append(lon)
                ys.append(lat)

    if geometry["type"] == "Polygon":
        collect(geometry["coordinates"])
    else:
        for polygon in geometry["coordinates"]:
            collect(polygon)

    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def merge_bounds(bounds_list: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in bounds_list),
        min(b[1] for b in bounds_list),
        max(b[2] for b in bounds_list),
        max(b[3] for b in bounds_list),
    )


def transformed_bbox(shape: shapefile.Shape, epsg: str) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = shape.bbox
    transformer = TRANSFORMERS[epsg]
    corners = [
        transformer.transform(xmin, ymin),
        transformer.transform(xmin, ymax),
        transformer.transform(xmax, ymin),
        transformer.transform(xmax, ymax),
    ]
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return min(xs), min(ys), max(xs), max(ys)


def load_oa_features() -> tuple[list[dict], dict[str, dict], str]:
    reader = read_zip_shapefile(OA_ZIP, encoding="utf-8")
    fields = fields_map(reader)
    idx_base = fields.index("BASE_DATE")
    idx_adm = fields.index("ADM_CD")
    idx_oa = fields.index("TOT_OA_CD")

    features = []
    lookup = {}
    district_names = []

    for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
        geometry = shape_to_geojson(shape, "EPSG:5179")
        feature = {
            "type": "Feature",
            "properties": {
                "baseDate": record[idx_base],
                "admCd": record[idx_adm],
                "oaCd": record[idx_oa],
            },
            "geometry": geometry,
        }
        features.append(feature)
        lookup[record[idx_oa]] = feature
        district_names.append(record[idx_adm][:5])

    district_code = Counter(district_names).most_common(1)[0][0]
    return features, lookup, district_code


def parse_metric_csvs(zip_path: Path) -> dict[str, dict]:
    metrics: dict[str, dict] = defaultdict(dict)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as fp:
                reader = csv.reader(io.TextIOWrapper(fp, encoding="cp949"))
                for row in reader:
                    if len(row) < 4:
                        continue
                    _, oa_code, metric_code, value = row[:4]
                    metrics[oa_code][metric_code] = value
    return metrics


def enrich_oa_features(oa_features: list[dict]) -> tuple[list[dict], dict]:
    population_metrics = parse_metric_csvs(POP_ZIP)
    household_metrics = parse_metric_csvs(HOUSEHOLD_ZIP)

    totals = {
        "population": 0,
        "households": 0,
        "avgAgeWeighted": 0.0,
        "densityMax": 0.0,
    }

    for feature in oa_features:
        oa_code = feature["properties"]["oaCd"]
        pop = population_metrics.get(oa_code, {})
        house = household_metrics.get(oa_code, {})

        total_population = int(float(pop.get("to_in_001", 0) or 0))
        avg_age = float(pop.get("to_in_002", 0) or 0)
        density = float(pop.get("to_in_003", 0) or 0)
        aging = float(pop.get("to_in_004", 0) or 0)
        child_dep = float(pop.get("to_in_006", 0) or 0)
        old_dep = float(pop.get("to_in_005", 0) or 0)
        households = int(float(house.get("to_ga_001", 0) or 0))

        feature["properties"].update(
            {
                "population": total_population,
                "avgAge": round(avg_age, 1),
                "populationDensity": round(density, 1),
                "agingIndex": round(aging, 1),
                "childDependency": round(child_dep, 1),
                "oldDependency": round(old_dep, 1),
                "households": households,
            }
        )

        totals["population"] += total_population
        totals["households"] += households
        totals["avgAgeWeighted"] += total_population * avg_age
        totals["densityMax"] = max(totals["densityMax"], density)

    totals["avgAge"] = round(
        totals["avgAgeWeighted"] / totals["population"], 1
    ) if totals["population"] else 0
    del totals["avgAgeWeighted"]
    totals["densityMax"] = round(totals["densityMax"], 1)
    return oa_features, totals


def load_district_polygons(oa_features: list[dict]) -> tuple[list[dict], tuple[float, float, float, float]]:
    bounds = [geom_bounds(feature["geometry"]) for feature in oa_features]
    return oa_features, merge_bounds(bounds)


def select_parcels(
    oa_features: list[dict], district_bounds: tuple[float, float, float, float]
) -> tuple[list[dict], dict]:
    reader = read_zip_shapefile(PARCEL_ZIP)
    fields = fields_map(reader)
    idx_pnu = fields.index("A1")
    idx_address = fields.index("A3")
    idx_lot = fields.index("A4")
    idx_label = fields.index("A5")
    idx_date = fields.index("A6")
    idx_sig = fields.index("A7")

    candidate_sig_codes: Counter[str] = Counter()
    for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
        bounds = transformed_bbox(shape, "EPSG:5186")
        if bbox_intersects(bounds, district_bounds):
            candidate_sig_codes[str(record[idx_sig]).strip()] += 1

    dominant_sig = candidate_sig_codes.most_common(1)[0][0]

    features = []
    address_counter: Counter[str] = Counter()
    dong_counter: Counter[str] = Counter()
    reader = read_zip_shapefile(PARCEL_ZIP)

    for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
        if str(record[idx_sig]).strip() != dominant_sig:
            continue
        bounds = transformed_bbox(shape, "EPSG:5186")
        if not bbox_intersects(bounds, district_bounds):
            continue
        center = bbox_center(shape, "EPSG:5186")
        if not any(point_in_geom(center, oa_feature["geometry"]) for oa_feature in oa_features):
            continue

        geometry = shape_to_geojson(shape, "EPSG:5186")
        if not geometry:
            continue
        address = str(record[idx_address]).strip()
        address_parts = address.split()
        dong_name = address_parts[2] if len(address_parts) >= 3 else address
        properties = {
            "pnu": str(record[idx_pnu]).strip(),
            "address": address,
            "dong": dong_name,
            "lotNumber": str(record[idx_lot]).strip(),
            "label": str(record[idx_label]).strip(),
            "updatedAt": str(record[idx_date]).strip(),
            "sigCd": str(record[idx_sig]).strip(),
        }
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": geometry,
            }
        )
        address_counter[address.split()[1] if len(address.split()) > 1 else address] += 1
        dong_counter[dong_name] += 1

    district_name = address_counter.most_common(1)[0][0] if address_counter else "11240"
    dong_summary = [
        {"name": name, "parcelCount": count}
        for name, count in dong_counter.most_common()
    ]
    return features, {"districtName": district_name, "sigCd": dominant_sig, "dongSummary": dong_summary}


def load_plan_layers(district_bounds: tuple[float, float, float, float]) -> tuple[list[dict], list[dict]]:
    features = []
    summaries: dict[str, dict] = {}
    color_idx = 0

    for zip_path in [*PLAN_ZIPS, DISTRICT_PLAN_ZIP]:
        epsg = "EPSG:5174" if zip_path.name == DISTRICT_PLAN_ZIP.name else "EPSG:5186"
        reader = read_zip_shapefile(zip_path)
        fields = fields_map(reader)

        if zip_path.name == DISTRICT_PLAN_ZIP.name:
            idx_name = fields.index("DGM_NM")
            idx_area = fields.index("DGM_AR")
            idx_sig = fields.index("SIGNGU_SE")
            for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
                geometry = shape_to_geojson(shape, epsg)
                if not geometry:
                    continue
                bounds = geom_bounds(geometry)
                if not bbox_intersects(bounds, district_bounds):
                    continue
                layer_name = str(record[idx_name]).strip()
                area = float(record[idx_area] or 0)
                if layer_name not in summaries:
                    summaries[layer_name] = {
                        "name": layer_name,
                        "source": zip_path.name,
                        "color": PLAN_COLORS[color_idx % len(PLAN_COLORS)],
                        "featureCount": 0,
                        "area": 0.0,
                    }
                    color_idx += 1
                color = summaries[layer_name]["color"]
                summaries[layer_name]["featureCount"] += 1
                summaries[layer_name]["area"] += area
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "layerName": layer_name,
                            "source": zip_path.name,
                            "areaSqm": round(area, 1),
                            "sigCd": str(record[idx_sig]).strip(),
                            "color": color,
                        },
                        "geometry": geometry,
                    }
                )
            continue

        idx_name = fields.index("A2")
        idx_area = fields.index("A4")
        idx_sig = fields.index("A6")
        for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
            geometry = shape_to_geojson(shape, epsg)
            if not geometry:
                continue
            bounds = geom_bounds(geometry)
            if not bbox_intersects(bounds, district_bounds):
                continue
            layer_name = str(record[idx_name]).strip()
            area = float(record[idx_area] or 0)
            if layer_name not in summaries:
                summaries[layer_name] = {
                    "name": layer_name,
                    "source": zip_path.name,
                    "color": PLAN_COLORS[color_idx % len(PLAN_COLORS)],
                    "featureCount": 0,
                    "area": 0.0,
                }
                color_idx += 1
            color = summaries[layer_name]["color"]
            summaries[layer_name]["featureCount"] += 1
            summaries[layer_name]["area"] += area
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "layerName": layer_name,
                        "source": zip_path.name,
                        "areaSqm": round(area, 1),
                        "sigCd": str(record[idx_sig]).strip(),
                        "color": color,
                    },
                    "geometry": geometry,
                }
            )

    summary_list = sorted(
        (
            {
                **summary,
                "area": round(summary["area"], 1),
            }
            for summary in summaries.values()
        ),
        key=lambda item: item["area"],
        reverse=True,
    )
    return features, summary_list


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def outputs_ready() -> bool:
    return all(path.exists() and path.stat().st_size > 0 for path in OUTPUT_FILES)


def summarize_land_use(parcel_features: list[dict], plan_features: list[dict]) -> list[dict]:
    plan_entries = []
    for feature in plan_features:
        geometry = feature["geometry"]
        if not geometry:
            continue
        plan_entries.append(
            {
                "name": feature["properties"]["layerName"],
                "bbox": geom_bounds(geometry),
                "geometry": geometry,
                "color": feature["properties"]["color"],
                "areaSqm": feature["properties"]["areaSqm"],
            }
        )

    counts: dict[str, dict] = {}

    for parcel in parcel_features:
        parcel_geom = parcel["geometry"]
        bounds = geom_bounds(parcel_geom)
        center = (
            (bounds[0] + bounds[2]) / 2.0,
            (bounds[1] + bounds[3]) / 2.0,
        )
        matched = None
        for plan in plan_entries:
            if not bbox_intersects(bounds, plan["bbox"]):
                continue
            if point_in_geom(center, plan["geometry"]):
                matched = plan
                break
        if matched is None:
            name = "미분류"
            color = "#94a3b8"
        else:
            name = matched["name"]
            color = matched["color"]

        current = counts.setdefault(
            name,
            {
                "name": name,
                "color": color,
                "parcelCount": 0,
            },
        )
        current["parcelCount"] += 1

    return sorted(counts.values(), key=lambda item: item["parcelCount"], reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 public/data 출력이 있어도 전처리를 다시 수행합니다.",
    )
    args = parser.parse_args()

    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)

    if outputs_ready() and not args.force:
        metadata = json.loads((PUBLIC_DATA / "metadata.json").read_text(encoding="utf-8"))
        print(json.dumps(
            {
                "status": "skipped",
                "reason": "existing generated data found",
                "title": metadata.get("title"),
                "stats": metadata.get("stats"),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    oa_features, _, district_code = load_oa_features()
    oa_features, census_totals = enrich_oa_features(oa_features)
    oa_features, district_bounds = load_district_polygons(oa_features)

    parcel_features, parcel_meta = select_parcels(oa_features, district_bounds)
    plan_features, plan_summary = load_plan_layers(district_bounds)
    land_use_summary = summarize_land_use(parcel_features, plan_features)

    district_label = f"{parcel_meta['districtName']} 데이터 뷰어"
    oa_geojson = {"type": "FeatureCollection", "features": oa_features}
    parcel_geojson = {"type": "FeatureCollection", "features": parcel_features}
    plan_geojson = {"type": "FeatureCollection", "features": plan_features}

    bounds = district_bounds
    metadata = {
        "title": district_label,
        "districtCode": district_code,
        "stats": {
            "oaCount": len(oa_features),
            "parcelCount": len(parcel_features),
            "planFeatureCount": len(plan_features),
            "population": census_totals["population"],
            "households": census_totals["households"],
            "avgAge": census_totals["avgAge"],
            "maxDensity": census_totals["densityMax"],
        },
        "bounds": {
            "west": bounds[0],
            "south": bounds[1],
            "east": bounds[2],
            "north": bounds[3],
        },
        "planSummary": plan_summary,
        "landUseSummary": land_use_summary,
        "dongSummary": parcel_meta["dongSummary"],
        "sources": [
            "AL_D002_11_20260508.zip",
            "bnd_oa_11240_2025_2Q.zip",
            "_census_reqdoc_1780054203703.zip",
            "_census_reqdoc_1780055403382.zip",
            "C_UQ161.zip",
            *[path.name for path in PLAN_ZIPS],
        ],
    }

    write_json(PUBLIC_DATA / "metadata.json", metadata)
    write_json(PUBLIC_DATA / "oa.geojson", oa_geojson)
    write_json(PUBLIC_DATA / "parcels.geojson", parcel_geojson)
    write_json(PUBLIC_DATA / "plans.geojson", plan_geojson)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
