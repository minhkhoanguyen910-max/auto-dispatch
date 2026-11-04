#!/bin/sh
# Downloads the OpenStreetMap roads for OSRM_REGIONS (Geofabrik paths, comma-separated) and merges them into
# /data/map.osm.pbf, from which the osrm (car) and osrm-foot (walking) services each prepare their own map.
# Runs again only when OSRM_REGIONS changes or one of the maps is missing. Existing maps are left alone, so
# they keep working while the other one is prepared.
set -e
cd /data
# A car map from before the walking map: `regions` is what it was built for
if [ -f map.osrm.mldgr ] && [ ! -f map.regions ] && [ -f regions ]; then
  cp regions map.regions
fi
built() { [ -f "$1.osrm.mldgr" ] && [ "$(cat "$1.regions" 2>/dev/null)" = "$OSRM_REGIONS" ]; }
if built map && built foot; then
  echo "Road maps are up to date"
  exit 0
fi
if [ -f map.osm.pbf ] && [ "$(cat regions 2>/dev/null)" = "$OSRM_REGIONS" ]; then
  echo "Already downloaded, waiting to be prepared"
  exit 0
fi

apt-get update -qq
apt-get install -y -qq --no-install-recommends wget ca-certificates osmium-tool >/dev/null
files=""
for region in $(echo "$OSRM_REGIONS" | tr ',' ' '); do
  name=$(basename "$region")
  echo "Downloading $region"
  wget -q -O "$name.osm.pbf" "https://download.geofabrik.de/$region-latest.osm.pbf"
  files="$files $name.osm.pbf"
done
osmium merge --overwrite -o new.osm.pbf $files
mv new.osm.pbf map.osm.pbf
rm -f $files
echo "$OSRM_REGIONS" > regions
