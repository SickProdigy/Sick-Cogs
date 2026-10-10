"""Validated content-version metadata for forward-compatible regional catalogs."""

from dataclasses import dataclass
import json
from pathlib import Path

DIMENSIONS=("typing","stats","learnsets","evolution_methods","abilities","items","weather","terrain")

class CatalogVersionError(ValueError):
    pass

@dataclass(frozen=True)
class CatalogVersion:
    key:str
    generation:int
    parent:str|None
    dimensions:dict

class CatalogVersions:
    def __init__(self,path=None):
        source=Path(path) if path else Path(__file__).with_name("catalog_versions.json")
        raw=json.loads(source.read_text(encoding="utf-8"))
        if int(raw.get("schema",0))!=1 or tuple(raw.get("dimensions",()))!=DIMENSIONS:
            raise CatalogVersionError("Catalog version manifest schema is invalid.")
        self.default=str(raw["default"]);self.releases={}
        for key,value in raw.get("releases",{}).items():
            dimensions=dict(value.get("dimensions",{}))
            if set(dimensions)!=set(DIMENSIONS):raise CatalogVersionError(f"Catalog release {key} does not declare every versioned dimension.")
            self.releases[key]=CatalogVersion(key,int(value["generation"]),value.get("parent"),dimensions)
        if self.default not in self.releases:raise CatalogVersionError("Default catalog release is missing.")
        for release in self.releases.values():
            if release.parent is not None and release.parent not in self.releases:raise CatalogVersionError(f"Unknown catalog parent for {release.key}.")

    def get(self,key):
        try:return self.releases[str(key)]
        except KeyError as exc:raise CatalogVersionError(f"Unknown catalog version: {key}") from exc

    def for_generation(self,generation,*,bundled=False):
        generation=max(1,min(9,int(generation)))
        key=self.default if bundled and generation==1 else f"pokeapi-gen{generation}-v1"
        return self.get(key)

CATALOG_VERSIONS=CatalogVersions()
