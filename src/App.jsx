import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const DEFAULT_TAB = "landuse";

const densityStops = [
  [20000, "#d7f171"],
  [50000, "#93d741"],
  [90000, "#3db18b"],
  [140000, "#2c7fb8"],
  [9999999, "#253494"],
];

function formatNumber(value) {
  return new Intl.NumberFormat("ko-KR").format(value ?? 0);
}

function formatArea(value) {
  return `${formatNumber(Math.round(value ?? 0))} ㎡`;
}

function getDensityColor(value) {
  for (const [limit, color] of densityStops) {
    if (value <= limit) return color;
  }
  return densityStops[densityStops.length - 1][1];
}

function createParcelStyle(opacity) {
  return {
    color: "#f8fafc",
    weight: 0.35,
    fillColor: "#f59e0b",
    fillOpacity: opacity,
  };
}

export default function App() {
  const mapRef = useRef(null);
  const mapObjectRef = useRef(null);
  const oaLayerRef = useRef(null);
  const parcelLayerRef = useRef(null);
  const planLayerRef = useRef(null);

  const [metadata, setMetadata] = useState(null);
  const [oaData, setOaData] = useState(null);
  const [parcelData, setParcelData] = useState(null);
  const [planData, setPlanData] = useState(null);
  const [activeTab, setActiveTab] = useState(DEFAULT_TAB);
  const [showOa, setShowOa] = useState(true);
  const [showParcels, setShowParcels] = useState(true);
  const [showPlans, setShowPlans] = useState(true);
  const [parcelOpacity, setParcelOpacity] = useState(0.74);
  const [hoveredOa, setHoveredOa] = useState(null);
  const [selectedParcel, setSelectedParcel] = useState(null);
  const [selectedPlanNames, setSelectedPlanNames] = useState([]);
  const [planSearch, setPlanSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let ignore = false;

    async function load() {
      setLoading(true);
      try {
        const [meta, oa, parcels, plans] = await Promise.all([
          fetch("/data/metadata.json").then((res) => res.json()),
          fetch("/data/oa.geojson").then((res) => res.json()),
          fetch("/data/parcels.geojson").then((res) => res.json()),
          fetch("/data/plans.geojson").then((res) => res.json()),
        ]);

        if (ignore) return;
        setMetadata(meta);
        setOaData(oa);
        setParcelData(parcels);
        setPlanData(plans);
        setSelectedPlanNames(meta.planSummary.slice(0, 8).map((item) => item.name));
      } catch (loadError) {
        if (!ignore) {
          setError(loadError instanceof Error ? loadError.message : "데이터를 불러오지 못했습니다.");
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      ignore = true;
    };
  }, []);

  const filteredPlanData = useMemo(() => {
    if (!planData) return null;
    return {
      ...planData,
      features: planData.features.filter((feature) =>
        selectedPlanNames.includes(feature.properties.layerName),
      ),
    };
  }, [planData, selectedPlanNames]);

  const visiblePlanSummary = useMemo(() => {
    if (!metadata) return [];
    return metadata.planSummary.filter((item) => selectedPlanNames.includes(item.name));
  }, [metadata, selectedPlanNames]);

  const visiblePlanOptions = useMemo(() => {
    if (!metadata) return [];
    const keyword = planSearch.trim();
    if (!keyword) return metadata.planSummary;
    return metadata.planSummary.filter((item) => item.name.includes(keyword));
  }, [metadata, planSearch]);

  useEffect(() => {
    if (!mapRef.current || mapObjectRef.current || !metadata) return;

    const map = L.map(mapRef.current, {
      zoomControl: false,
      preferCanvas: true,
    });

    mapObjectRef.current = map;

    L.control
      .zoom({
        position: "bottomright",
      })
      .addTo(map);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 19,
    }).addTo(map);

    const { south, west, north, east } = metadata.bounds;
    map.fitBounds([
      [south, west],
      [north, east],
    ]);

    return () => {
      map.remove();
      mapObjectRef.current = null;
    };
  }, [metadata]);

  useEffect(() => {
    const map = mapObjectRef.current;
    if (!map || !oaData) return;

    if (oaLayerRef.current) {
      oaLayerRef.current.remove();
      oaLayerRef.current = null;
    }

    if (!showOa) return;

    const layer = L.geoJSON(oaData, {
      style: (feature) => ({
        color: hoveredOa?.oaCd === feature?.properties?.oaCd ? "#f8fafc" : "#0f172a",
        weight: hoveredOa?.oaCd === feature?.properties?.oaCd ? 1.6 : 0.7,
        fillColor: getDensityColor(feature?.properties?.populationDensity ?? 0),
        fillOpacity: 0.45,
      }),
      onEachFeature: (feature, leafletLayer) => {
        leafletLayer.on({
          mouseover: () => setHoveredOa(feature.properties),
          mouseout: () => setHoveredOa(null),
          click: () => {
            setHoveredOa(feature.properties);
            setActiveTab("census");
          },
        });
      },
    }).addTo(map);

    oaLayerRef.current = layer;
  }, [oaData, showOa, hoveredOa]);

  useEffect(() => {
    const map = mapObjectRef.current;
    if (!map || !parcelData) return;

    if (parcelLayerRef.current) {
      parcelLayerRef.current.remove();
      parcelLayerRef.current = null;
    }

    if (!showParcels) return;

    const layer = L.geoJSON(parcelData, {
      style: createParcelStyle(parcelOpacity),
      renderer: L.canvas(),
      onEachFeature: (feature, leafletLayer) => {
        leafletLayer.on("click", () => {
          setSelectedParcel(feature.properties);
          setActiveTab("parcel");
        });
      },
    }).addTo(map);

    parcelLayerRef.current = layer;
  }, [parcelData, showParcels, parcelOpacity]);

  useEffect(() => {
    const map = mapObjectRef.current;
    if (!map || !filteredPlanData) return;

    if (planLayerRef.current) {
      planLayerRef.current.remove();
      planLayerRef.current = null;
    }

    if (!showPlans) return;

    const layer = L.geoJSON(filteredPlanData, {
      style: (feature) => ({
        color: feature?.properties?.color ?? "#ec4899",
        weight: 2,
        fillColor: feature?.properties?.color ?? "#ec4899",
        fillOpacity: 0.15,
      }),
      onEachFeature: (feature, leafletLayer) => {
        const props = feature.properties;
        leafletLayer.bindTooltip(
          `<strong>${props.layerName}</strong><br/>면적: ${formatNumber(Math.round(props.areaSqm))}㎡`,
          { sticky: true },
        );
        leafletLayer.on("click", () => setActiveTab("plans"));
      },
    }).addTo(map);

    planLayerRef.current = layer;
  }, [filteredPlanData, showPlans]);

  function togglePlan(name) {
    setSelectedPlanNames((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    );
  }

  if (loading) {
    return <div className="screen-state">데이터를 정리하고 지도를 준비하는 중입니다.</div>;
  }

  if (error || !metadata || !oaData || !parcelData || !planData) {
    return <div className="screen-state">초기화 실패: {error || "필수 데이터가 없습니다."}</div>;
  }

  return (
    <div className="app-shell">
      <aside className="panel panel-left">
        <div className="panel-head">
          <p className="eyebrow">Land Intelligence Lab</p>
          <h1>{metadata.title}</h1>
          <p className="subtitle">
            집계구 통계, 필지, 도시계획 레이어를 한 화면에서 확인하는 실습형 로컬 뷰어
          </p>
        </div>

        <div className="stat-grid">
          <article>
            <span>집계구</span>
            <strong>{formatNumber(metadata.stats.oaCount)}</strong>
          </article>
          <article>
            <span>필지</span>
            <strong>{formatNumber(metadata.stats.parcelCount)}</strong>
          </article>
          <article>
            <span>총인구</span>
            <strong>{formatNumber(metadata.stats.population)}</strong>
          </article>
          <article>
            <span>평균나이</span>
            <strong>{metadata.stats.avgAge}세</strong>
          </article>
        </div>

        <section className="control-block">
          <div className="block-title">표시 레이어</div>
          <label className="toggle">
            <input type="checkbox" checked={showOa} onChange={(event) => setShowOa(event.target.checked)} />
            <span>집계구 통계</span>
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={showParcels}
              onChange={(event) => setShowParcels(event.target.checked)}
            />
            <span>필지 경계</span>
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={showPlans}
              onChange={(event) => setShowPlans(event.target.checked)}
            />
            <span>도시계획 레이어</span>
          </label>
        </section>

        <section className="control-block">
          <div className="range-head">
            <div className="block-title">필지 투명도</div>
            <span>{Math.round(parcelOpacity * 100)}%</span>
          </div>
          <input
            className="range"
            type="range"
            min="0.1"
            max="0.95"
            step="0.05"
            value={parcelOpacity}
            onChange={(event) => setParcelOpacity(Number(event.target.value))}
          />
        </section>

        <section className="control-block plan-list">
          <div className="range-head">
            <div className="block-title">도시계획 선택</div>
            <span>{selectedPlanNames.length}개 사용 중</span>
          </div>
          <input
            className="search-input"
            type="text"
            value={planSearch}
            onChange={(event) => setPlanSearch(event.target.value)}
            placeholder="레이어명 검색"
          />
          {visiblePlanOptions.map((item) => (
            <label className="plan-item" key={item.name}>
              <input
                type="checkbox"
                checked={selectedPlanNames.includes(item.name)}
                onChange={() => togglePlan(item.name)}
              />
              <span className="swatch" style={{ backgroundColor: item.color }} />
              <span className="plan-name">{item.name}</span>
            </label>
          ))}
        </section>

        <section className="control-block">
          <div className="range-head">
            <div className="block-title">동별 필지 수</div>
            <span>{metadata.dongSummary.length}개 동</span>
          </div>
          <div className="summary-list compact">
            {metadata.dongSummary.map((item) => (
              <article className="summary-item" key={item.name}>
                <strong>{item.name}</strong>
                <span>{formatNumber(item.parcelCount)} 필지</span>
              </article>
            ))}
          </div>
        </section>
      </aside>

      <main className="map-stage">
        <div ref={mapRef} className="map-view" />
        <div className="map-caption">
          <span>기준 구역 코드 {metadata.districtCode}</span>
          <span>최대 인구밀도 {formatNumber(metadata.stats.maxDensity)}명/㎢</span>
        </div>
      </main>

      <aside className="panel panel-right">
        <div className="tabs">
          <button
            type="button"
            className={activeTab === "landuse" ? "active" : ""}
            onClick={() => setActiveTab("landuse")}
          >
            토지이용
          </button>
          <button
            type="button"
            className={activeTab === "census" ? "active" : ""}
            onClick={() => setActiveTab("census")}
          >
            집계구 통계
          </button>
          <button
            type="button"
            className={activeTab === "parcel" ? "active" : ""}
            onClick={() => setActiveTab("parcel")}
          >
            필지 정보
          </button>
        </div>

        {activeTab === "landuse" && (
          <section className="tab-panel">
            <h2>토지이용 현황표</h2>
            <p className="panel-copy">
              필지 중심점 기준으로 도시계획 레이어에 귀속시킨 요약입니다.
            </p>
            <div className="summary-list">
              {metadata.landUseSummary.slice(0, 18).map((item) => {
                const ratio = (item.parcelCount / metadata.stats.parcelCount) * 100;
                return (
                  <article className="summary-item" key={item.name}>
                    <div className="summary-head">
                      <span className="swatch" style={{ backgroundColor: item.color }} />
                      <strong>{item.name}</strong>
                    </div>
                    <span>{formatNumber(item.parcelCount)} 필지</span>
                    <span>{ratio.toFixed(1)}%</span>
                  </article>
                );
              })}
            </div>
          </section>
        )}

        {activeTab === "census" && (
          <section className="tab-panel">
            <h2>집계구 통계</h2>
            <p className="panel-copy">
              집계구를 클릭하면 상세 인구·가구 지표를 확인할 수 있습니다.
            </p>
            {hoveredOa ? (
              <div className="metric-stack">
                <article>
                  <span>집계구 코드</span>
                  <strong>{hoveredOa.oaCd}</strong>
                </article>
                <article>
                  <span>총인구</span>
                  <strong>{formatNumber(hoveredOa.population)}명</strong>
                </article>
                <article>
                  <span>가구수</span>
                  <strong>{formatNumber(hoveredOa.households)}가구</strong>
                </article>
                <article>
                  <span>평균나이</span>
                  <strong>{hoveredOa.avgAge}세</strong>
                </article>
                <article>
                  <span>인구밀도</span>
                  <strong>{formatNumber(hoveredOa.populationDensity)}명/㎢</strong>
                </article>
                <article>
                  <span>노령화지수</span>
                  <strong>{hoveredOa.agingIndex}</strong>
                </article>
              </div>
            ) : (
              <div className="empty-card">지도 위 집계구를 가리키거나 클릭하세요.</div>
            )}
          </section>
        )}

        {activeTab === "parcel" && (
          <section className="tab-panel">
            <h2>필지 정보</h2>
            <p className="panel-copy">필지 폴리곤을 클릭하면 주소와 PNU를 확인할 수 있습니다.</p>
            {selectedParcel ? (
              <div className="metric-stack">
                <article>
                  <span>PNU</span>
                  <strong>{selectedParcel.pnu}</strong>
                </article>
                <article>
                  <span>주소</span>
                  <strong>{selectedParcel.address}</strong>
                </article>
                <article>
                  <span>행정동</span>
                  <strong>{selectedParcel.dong}</strong>
                </article>
                <article>
                  <span>지번</span>
                  <strong>{selectedParcel.lotNumber}</strong>
                </article>
                <article>
                  <span>표기</span>
                  <strong>{selectedParcel.label}</strong>
                </article>
                <article>
                  <span>최종갱신</span>
                  <strong>{selectedParcel.updatedAt}</strong>
                </article>
              </div>
            ) : (
              <div className="empty-card">지도 위 필지를 클릭하세요.</div>
            )}
            <div className="subpanel">
              <h3>선택된 도시계획 레이어</h3>
              <div className="summary-list compact">
                {visiblePlanSummary.slice(0, 12).map((item) => (
                  <article className="summary-item" key={item.name}>
                    <div className="summary-head">
                      <span className="swatch" style={{ backgroundColor: item.color }} />
                      <strong>{item.name}</strong>
                    </div>
                    <span>{formatNumber(item.featureCount)}개</span>
                    <span>{formatArea(item.area)}</span>
                  </article>
                ))}
              </div>
            </div>
          </section>
        )}
      </aside>
    </div>
  );
}
