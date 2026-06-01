+from __future__ import annotations
      2 +
      3 +import csv
      4 +import io
      5 +import json
      6 +import math
      7 +import zipfile
      8 +from collections import Counter, defaultdict
      9 +from pathlib import Path
     10 +
     11 +import shapefile
     12 +from pyproj import Transformer
     13 +
     14 +
     15 +ROOT = Path(__file__).resolve().parent.parent
     16 +PUBLIC_DATA = ROOT / "public" / "data"
     17 +
     18 +PARCEL_ZIP = ROOT / "AL_D002_11_20260508.zip"
     19 +OA_ZIP = ROOT / "bnd_oa_11240_2025_2Q.zip"
     20 +PLAN_ZIPS = sorted(ROOT.glob("CH_D*_20260528.zip"))
     21 +DISTRICT_PLAN_ZIP = ROOT / "C_UQ161.zip"
     22 +POP_ZIP = ROOT / "_census_reqdoc_1780054203703.zip"
     23 +HOUSEHOLD_ZIP = ROOT / "_census_reqdoc_1780055403382.zip"
     24 +
     25 +
     26 +TRANSFORMERS = {
     27 +    "EPSG:5186": Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True),
     28 +    "EPSG:5179": Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True),
     29 +    "EPSG:5174": Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True),
     30 +}
     31 +
     32 +PLAN_COLORS = [
     33 +    "#fde725",
     34 +    "#7ad151",
     35 +    "#22a884",
     36 +    "#2a788e",
     37 +    "#414487",
     38 +    "#440154",
     39 +    "#f9844a",
     40 +    "#4d908e",
     41 +    "#277da1",
     42 +    "#f15bb5",
     43 +    "#fee440",
     44 +    "#9b5de5",
     45 +]
     46 +
     47 +
     48 +def read_zip_shapefile(zip_path: Path, encoding: str = "cp949") -> shapefile.Reader:
     49 +    with zipfile.ZipFile(zip_path) as zf:
     50 +        payloads: dict[str, bytes] = {}
     51 +        for name in zf.namelist():
     52 +            suffix = Path(name).suffix.lower()
     53 +            if suffix in {".shp", ".shx", ".dbf"}:
     54 +                payloads[suffix] = zf.read(name)
     55 +    return shapefile.Reader(
     56 +        shp=io.BytesIO(payloads[".shp"]),
     57 +        shx=io.BytesIO(payloads[".shx"]),
     58 +        dbf=io.BytesIO(payloads[".dbf"]),
     59 +        encoding=encoding,
     60 +    )
     61 +
     62 +
     63 +def fields_map(reader: shapefile.Reader) -> list[str]:
     64 +    return [field[0] for field in reader.fields[1:]]
     65 +
     66 +
     67 +def ring_area(points: list[tuple[float, float]]) -> float:
     68 +    area = 0.0
     69 +    for idx in range(len(points)):
     70 +        x1, y1 = points[idx]
     71 +        x2, y2 = points[(idx + 1) % len(points)]
     72 +        area += x1 * y2 - x2 * y1
     73 +    return area / 2.0
     74 +
     75 +
     76 +def transform_points(points: list[tuple[float, float]], epsg: str) -> list[list[float]]:
     77 +    transformer = TRANSFORMERS[epsg]
     78 +    coords = []
     79 +    for x, y in points:
     80 +        lon, lat = transformer.transform(x, y)
     81 +        coords.append([round(lon, 6), round(lat, 6)])
     82 +    if coords and coords[0] != coords[-1]:
     83 +        coords.append(coords[0])
     84 +    return coords
     85 +
     86 +
     87 +def shape_to_geojson(shape: shapefile.Shape, epsg: str) -> dict:
     88 +    parts = list(shape.parts) + [len(shape.points)]
     89 +    rings = []
     90 +    for start, end in zip(parts[:-1], parts[1:]):
     91 +        ring = transform_points(shape.points[start:end], epsg)
     92 +        if len(ring) >= 4:
     93 +            rings.append(ring)
     94 +
     95 +    outers: list[list[list[float]]] = []
     96 +    holes_by_outer: list[list[list[list[float]]]] = []
     97 +    for ring in rings:
     98 +        if ring_area([(x, y) for x, y in ring[:-1]]) < 0:
     99 +            outers.append(ring)
    100 +            holes_by_outer.append([])
    101 +        elif holes_by_outer:
    102 +            holes_by_outer[-1].append(ring)
    103 +        else:
    104 +            outers.append(ring)
    105 +            holes_by_outer.append([])
    106 +
    107 +    polygons = []
    108 +    for outer, holes in zip(outers, holes_by_outer):
    109 +        polygons.append([outer, *holes])
    110 +
    111 +    if len(polygons) == 1:
    112 +        return {"type": "Polygon", "coordinates": polygons[0]}
    113 +    return {"type": "MultiPolygon", "coordinates": polygons}
    114 +
    115 +
    116 +def bbox_center(shape: shapefile.Shape, epsg: str) -> tuple[float, float]:
    117 +    xmin, ymin, xmax, ymax = shape.bbox
    118 +    transformer = TRANSFORMERS[epsg]
    119 +    lon, lat = transformer.transform((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
    120 +    return lon, lat
    121 +
    122 +
    123 +def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    124 +    x, y = point
    125 +    inside = False
    126 +    for i in range(len(ring) - 1):
    127 +        x1, y1 = ring[i]
    128 +        x2, y2 = ring[i + 1]
    129 +        intersects = ((y1 > y) != (y2 > y)) and (
    130 +            x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
    131 +        )
    132 +        if intersects:
    133 +            inside = not inside
    134 +    return inside
    135 +
    136 +
    137 +def point_in_geom(point: tuple[float, float], geometry: dict) -> bool:
    138 +    if geometry["type"] == "Polygon":
    139 +        rings = geometry["coordinates"]
    140 +        if not point_in_ring(point, rings[0]):
    141 +            return False
    142 +        for hole in rings[1:]:
    143 +            if point_in_ring(point, hole):
    144 +                return False
    145 +        return True
    146 +
    147 +    for polygon in geometry["coordinates"]:
    148 +        if not point_in_ring(point, polygon[0]):
    149 +            continue
    150 +        if any(point_in_ring(point, hole) for hole in polygon[1:]):
    151 +            continue
    152 +        return True
    153 +    return False
    154 +
    155 +
    156 +def geom_bounds(geometry: dict) -> tuple[float, float, float, float]:
    157 +    xs: list[float] = []
    158 +    ys: list[float] = []
    159 +
    160 +    def collect(rings: list[list[list[float]]]) -> None:
    161 +        for ring in rings:
    162 +            for lon, lat in ring:
    163 +                xs.append(lon)
    164 +                ys.append(lat)
    165 +
    166 +    if geometry["type"] == "Polygon":
    167 +        collect(geometry["coordinates"])
    168 +    else:
    169 +        for polygon in geometry["coordinates"]:
    170 +            collect(polygon)
    171 +
    172 +    return min(xs), min(ys), max(xs), max(ys)
    173 +
    174 +
    175 +def bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, floa
         t]) -> bool:
    176 +    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])
    177 +
    178 +
    179 +def merge_bounds(bounds_list: list[tuple[float, float, float, float]]) -> tuple[float, float
         , float, float]:
    180 +    return (
    181 +        min(b[0] for b in bounds_list),
    182 +        min(b[1] for b in bounds_list),
    183 +        max(b[2] for b in bounds_list),
    184 +        max(b[3] for b in bounds_list),
    185 +    )
    186 +
    187 +
    188 +def load_oa_features() -> tuple[list[dict], dict[str, dict], str]:
    189 +    reader = read_zip_shapefile(OA_ZIP, encoding="utf-8")
    190 +    fields = fields_map(reader)
    191 +    idx_base = fields.index("BASE_DATE")
    192 +    idx_adm = fields.index("ADM_CD")
    193 +    idx_oa = fields.index("TOT_OA_CD")
    194 +
    195 +    features = []
    196 +    lookup = {}
    197 +    district_names = []
    198 +
    199 +    for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
    200 +        geometry = shape_to_geojson(shape, "EPSG:5179")
    201 +        feature = {
    202 +            "type": "Feature",
    203 +            "properties": {
    204 +                "baseDate": record[idx_base],
    205 +                "admCd": record[idx_adm],
    206 +                "oaCd": record[idx_oa],
    207 +            },
    208 +            "geometry": geometry,
    209 +        }
    210 +        features.append(feature)
    211 +        lookup[record[idx_oa]] = feature
    212 +        district_names.append(record[idx_adm][:5])
    213 +
    214 +    district_code = Counter(district_names).most_common(1)[0][0]
    215 +    return features, lookup, district_code
    216 +
    217 +
    218 +def parse_metric_csvs(zip_path: Path) -> dict[str, dict]:
    219 +    metrics: dict[str, dict] = defaultdict(dict)
    220 +    with zipfile.ZipFile(zip_path) as zf:
    221 +        for name in zf.namelist():
    222 +            if not name.lower().endswith(".csv"):
    223 +                continue
    224 +            with zf.open(name) as fp:
    225 +                reader = csv.reader(io.TextIOWrapper(fp, encoding="cp949"))
    226 +                for row in reader:
    227 +                    if len(row) < 4:
    228 +                        continue
    229 +                    _, oa_code, metric_code, value = row[:4]
    230 +                    metrics[oa_code][metric_code] = value
    231 +    return metrics
    232 +
    233 +
    234 +def enrich_oa_features(oa_features: list[dict]) -> tuple[list[dict], dict]:
    235 +    population_metrics = parse_metric_csvs(POP_ZIP)
    236 +    household_metrics = parse_metric_csvs(HOUSEHOLD_ZIP)
    237 +
    238 +    totals = {
    239 +        "population": 0,
    240 +        "households": 0,
    241 +        "avgAgeWeighted": 0.0,
    242 +        "densityMax": 0.0,
    243 +    }
    244 +
    245 +    for feature in oa_features:
    246 +        oa_code = feature["properties"]["oaCd"]
    247 +        pop = population_metrics.get(oa_code, {})
    248 +        house = household_metrics.get(oa_code, {})
    249 +
    250 +        total_population = int(float(pop.get("to_in_001", 0) or 0))
    251 +        avg_age = float(pop.get("to_in_002", 0) or 0)
    252 +        density = float(pop.get("to_in_003", 0) or 0)
    253 +        aging = float(pop.get("to_in_004", 0) or 0)
    254 +        child_dep = float(pop.get("to_in_006", 0) or 0)
    255 +        old_dep = float(pop.get("to_in_005", 0) or 0)
    256 +        households = int(float(house.get("to_ga_001", 0) or 0))
    257 +
    258 +        feature["properties"].update(
    259 +            {
    260 +                "population": total_population,
    261 +                "avgAge": round(avg_age, 1),
    262 +                "populationDensity": round(density, 1),
    263 +                "agingIndex": round(aging, 1),
    264 +                "childDependency": round(child_dep, 1),
    265 +                "oldDependency": round(old_dep, 1),
    266 +                "households": households,
    267 +            }
    268 +        )
    269 +
    270 +        totals["population"] += total_population
    271 +        totals["households"] += households
    272 +        totals["avgAgeWeighted"] += total_population * avg_age
    273 +        totals["densityMax"] = max(totals["densityMax"], density)
    274 +
    275 +    totals["avgAge"] = round(
    276 +        totals["avgAgeWeighted"] / totals["population"], 1
    277 +    ) if totals["population"] else 0
    278 +    del totals["avgAgeWeighted"]
    279 +    totals["densityMax"] = round(totals["densityMax"], 1)
    280 +    return oa_features, totals
    281 +
    282 +
    283 +def load_district_polygons(oa_features: list[dict]) -> tuple[list[dict], tuple[float, float,
          float, float]]:
    284 +    bounds = [geom_bounds(feature["geometry"]) for feature in oa_features]
    285 +    return oa_features, merge_bounds(bounds)
    286 +
    287 +
    288 +def select_parcels(oa_features: list[dict], district_bounds: tuple[float, float, float, floa
         t]) -> tuple[list[dict], dict]:
    289 +    reader = read_zip_shapefile(PARCEL_ZIP)
    290 +    fields = fields_map(reader)
    291 +    idx_pnu = fields.index("A1")
    292 +    idx_address = fields.index("A3")
    293 +    idx_lot = fields.index("A4")
    294 +    idx_label = fields.index("A5")
    295 +    idx_date = fields.index("A6")
    296 +    idx_sig = fields.index("A7")
    297 +
    298 +    features = []
    299 +    address_counter: Counter[str] = Counter()
    300 +
    301 +    for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
    302 +        center = bbox_center(shape, "EPSG:5186")
    303 +        if not (district_bounds[0] <= center[0] <= district_bounds[2] and district_bounds[1]
          <= center[1] <= district_bounds[3]):
    304 +            continue
    305 +        if not any(point_in_geom(center, oa_feature["geometry"]) for oa_feature in oa_featur
         es):
    306 +            continue
    307 +
    308 +        geometry = shape_to_geojson(shape, "EPSG:5186")
    309 +        address = str(record[idx_address]).strip()
    310 +        properties = {
    311 +            "pnu": str(record[idx_pnu]).strip(),
    312 +            "address": address,
    313 +            "lotNumber": str(record[idx_lot]).strip(),
    314 +            "label": str(record[idx_label]).strip(),
    315 +            "updatedAt": str(record[idx_date]).strip(),
    316 +            "sigCd": str(record[idx_sig]).strip(),
    317 +        }
    318 +        features.append(
    319 +            {
    320 +                "type": "Feature",
    321 +                "properties": properties,
    322 +                "geometry": geometry,
    323 +            }
    324 +        )
    325 +        address_counter[address.split()[1] if len(address.split()) > 1 else address] += 1
    326 +
    327 +    district_name = address_counter.most_common(1)[0][0] if address_counter else "11240"
    328 +    return features, {"districtName": district_name}
    329 +
    330 +
    331 +def load_plan_layers(district_bounds: tuple[float, float, float, float]) -> tuple[list[dict]
         , list[dict]]:
    332 +    features = []
    333 +    summaries: dict[str, dict] = {}
    334 +    color_idx = 0
    335 +
    336 +    for zip_path in [*PLAN_ZIPS, DISTRICT_PLAN_ZIP]:
    337 +        epsg = "EPSG:5174" if zip_path.name == DISTRICT_PLAN_ZIP.name else "EPSG:5186"
    338 +        reader = read_zip_shapefile(zip_path)
    339 +        fields = fields_map(reader)
    340 +
    341 +        if zip_path.name == DISTRICT_PLAN_ZIP.name:
    342 +            idx_name = fields.index("DGM_NM")
    343 +            idx_area = fields.index("DGM_AR")
    344 +            idx_sig = fields.index("SIGNGU_SE")
    345 +            for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
    346 +                geometry = shape_to_geojson(shape, epsg)
    347 +                bounds = geom_bounds(geometry)
    348 +                if not bbox_intersects(bounds, district_bounds):
    349 +                    continue
    350 +                layer_name = str(record[idx_name]).strip()
    351 +                area = float(record[idx_area] or 0)
    352 +                color = summaries.setdefault(
    353 +                    layer_name,
    354 +                    {
    355 +                        "name": layer_name,
    356 +                        "source": zip_path.name,
    357 +                        "color": PLAN_COLORS[color_idx % len(PLAN_COLORS)],
    358 +                        "featureCount": 0,
    359 +                        "area": 0.0,
    360 +                    },
    361 +                )["color"]
    362 +                if layer_name not in [summary["name"] for summary in summaries.values()]:
    363 +                    color_idx += 1
    364 +                summaries[layer_name]["featureCount"] += 1
    365 +                summaries[layer_name]["area"] += area
    366 +                features.append(
    367 +                    {
    368 +                        "type": "Feature",
    369 +                        "properties": {
    370 +                            "layerName": layer_name,
    371 +                            "source": zip_path.name,
    372 +                            "areaSqm": round(area, 1),
    373 +                            "sigCd": str(record[idx_sig]).strip(),
    374 +                            "color": color,
    375 +                        },
    376 +                        "geometry": geometry,
    377 +                    }
    378 +                )
    379 +            continue
    380 +
    381 +        idx_name = fields.index("A2")
    382 +        idx_area = fields.index("A4")
    383 +        idx_sig = fields.index("A6")
    384 +        for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
    385 +            geometry = shape_to_geojson(shape, epsg)
    386 +            bounds = geom_bounds(geometry)
    387 +            if not bbox_intersects(bounds, district_bounds):
    388 +                continue
    389 +            layer_name = str(record[idx_name]).strip()
    390 +            area = float(record[idx_area] or 0)
    391 +            color = summaries.setdefault(
    392 +                layer_name,
    393 +                {
    394 +                    "name": layer_name,
    395 +                    "source": zip_path.name,
    396 +                    "color": PLAN_COLORS[color_idx % len(PLAN_COLORS)],
    397 +                    "featureCount": 0,
    398 +                    "area": 0.0,
    399 +                },
    400 +            )["color"]
    401 +            if layer_name not in [summary["name"] for summary in summaries.values()]:
    402 +                color_idx += 1
    403 +            summaries[layer_name]["featureCount"] += 1
    404 +            summaries[layer_name]["area"] += area
    405 +            features.append(
    406 +                {
    407 +                    "type": "Feature",
    408 +                    "properties": {
    409 +                        "layerName": layer_name,
    410 +                        "source": zip_path.name,
    411 +                        "areaSqm": round(area, 1),
    412 +                        "sigCd": str(record[idx_sig]).strip(),
    413 +                        "color": color,
    414 +                    },
    415 +                    "geometry": geometry,
    416 +                }
    417 +            )
    418 +
    419 +    summary_list = sorted(
    420 +        (
    421 +            {
    422 +                **summary,
    423 +                "area": round(summary["area"], 1),
    424 +            }
    425 +            for summary in summaries.values()
    426 +        ),
    427 +        key=lambda item: item["area"],
    428 +        reverse=True,
    429 +    )
    430 +    return features, summary_list
    431 +
    432 +
    433 +def write_json(path: Path, payload: dict) -> None:
    434 +    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    435 +
    436 +
    437 +def main() -> None:
    438 +    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    439 +
    440 +    oa_features, _, district_code = load_oa_features()
    441 +    oa_features, census_totals = enrich_oa_features(oa_features)
    442 +    oa_features, district_bounds = load_district_polygons(oa_features)
    443 +
    444 +    parcel_features, parcel_meta = select_parcels(oa_features, district_bounds)
    445 +    plan_features, plan_summary = load_plan_layers(district_bounds)
    446 +
    447 +    district_label = f"{parcel_meta['districtName']} 데이터 뷰어"
    448 +    oa_geojson = {"type": "FeatureCollection", "features": oa_features}
    449 +    parcel_geojson = {"type": "FeatureCollection", "features": parcel_features}
    450 +    plan_geojson = {"type": "FeatureCollection", "features": plan_features}
    451 +
    452 +    bounds = district_bounds
    453 +    metadata = {
    454 +        "title": district_label,
    455 +        "districtCode": district_code,
    456 +        "stats": {
    457 +            "oaCount": len(oa_features),
    458 +            "parcelCount": len(parcel_features),
    459 +            "planFeatureCount": len(plan_features),
    460 +            "population": census_totals["population"],
    461 +            "households": census_totals["households"],
    462 +            "avgAge": census_totals["avgAge"],
    463 +            "maxDensity": census_totals["densityMax"],
    464 +        },
    465 +        "bounds": {
    466 +            "west": bounds[0],
    467 +            "south": bounds[1],
    468 +            "east": bounds[2],
    469 +            "north": bounds[3],
    470 +        },
    471 +        "planSummary": plan_summary,
    472 +        "sources": [
    473 +            "AL_D002_11_20260508.zip",
    474 +            "bnd_oa_11240_2025_2Q.zip",
    475 +            "_census_reqdoc_1780054203703.zip",
    476 +            "_census_reqdoc_1780055403382.zip",
    477 +            "C_UQ161.zip",
    478 +            *[path.name for path in PLAN_ZIPS],
    479 +        ],
    480 +    }
    481 +
    482 +    write_json(PUBLIC_DATA / "metadata.json", metadata)
    491 +    main()