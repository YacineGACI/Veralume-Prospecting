"""Where to look, nearest first: a circle around Strasbourg, then whole French
départements by distance. Never across the border: the register is French, OSM queries
are clipped to France, and foreign domains are dropped from searches."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ring:
    key: str
    label: str
    departement: str | None = None
    radius_km: float | None = None
    towns: list[str] = field(default_factory=list)


RINGS = [
    Ring("circle-30", "Strasbourg, 30 km", radius_km=30, towns=[
        "Strasbourg", "Illkirch-Graffenstaden", "Schiltigheim", "Lingolsheim", "Haguenau",
        "Molsheim", "Obernai", "Erstein", "Brumath", "Saverne", "Sélestat", "Bischwiller"]),
    Ring("circle-50", "Strasbourg, 50 km", radius_km=50, towns=["Sarrebourg", "Wissembourg", "Barr"]),
    Ring("67", "Bas-Rhin", "67"),
    Ring("68", "Haut-Rhin", "68", towns=["Colmar", "Mulhouse", "Saint-Louis", "Guebwiller"]),
    Ring("57", "Moselle", "57", towns=["Metz", "Thionville", "Sarreguemines", "Forbach"]),
    Ring("88", "Vosges", "88", towns=["Épinal", "Saint-Dié-des-Vosges", "Gérardmer"]),
    Ring("54", "Meurthe-et-Moselle", "54", towns=["Nancy", "Lunéville", "Pont-à-Mousson"]),
    Ring("90", "Territoire de Belfort", "90", towns=["Belfort"]),
    Ring("70", "Haute-Saône", "70", towns=["Vesoul"]),
    Ring("55", "Meuse", "55", towns=["Verdun", "Bar-le-Duc"]),
    Ring("25", "Doubs", "25", towns=["Besançon", "Montbéliard", "Pontarlier"]),
    Ring("52", "Haute-Marne", "52", towns=["Chaumont", "Saint-Dizier"]),
    Ring("39", "Jura", "39", towns=["Lons-le-Saunier", "Dole"]),
    Ring("08", "Ardennes", "08", towns=["Charleville-Mézières", "Sedan"]),
    Ring("51", "Marne", "51", towns=["Reims", "Châlons-en-Champagne", "Épernay"]),
    Ring("21", "Côte-d'Or", "21", towns=["Dijon", "Beaune"]),
    Ring("10", "Aube", "10", towns=["Troyes"]),
    Ring("71", "Saône-et-Loire", "71", towns=["Chalon-sur-Saône", "Mâcon"]),
    Ring("01", "Ain", "01", towns=["Bourg-en-Bresse"]),
    Ring("02", "Aisne", "02", towns=["Saint-Quentin", "Laon"]),
    Ring("89", "Yonne", "89", towns=["Auxerre", "Sens"]),
    Ring("74", "Haute-Savoie", "74", towns=["Annecy", "Annemasse"]),
]  # fmt: skip

FOREIGN_TLDS = (".de", ".ch", ".lu", ".be", ".at", ".it", ".nl")
