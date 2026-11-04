#!/bin/sh
# Prepares one OSRM map from a newly downloaded /data/map.osm.pbf (a few minutes), unless it's already built
# for the downloaded regions, then serves it. $1 is the profile: car (files map.osrm.*, the default) or foot
# (foot.osrm.*). The download is deleted once both maps are built from it.
set -e
profile=${1:-car}
if [ "$profile" = car ]; then name=map; else name=$profile; fi
cd /data
if [ -f map.osm.pbf ] && { [ ! -f $name.osrm.mldgr ] || [ "$(cat $name.regions 2>/dev/null)" != "$(cat regions)" ]; }; then
  rm -f $name.regions
  # osrm-extract names its files after the input file
  [ $name = map ] || ln -sf map.osm.pbf $name.osm.pbf
  osrm-extract -p /opt/$profile.lua $name.osm.pbf
  [ $name = map ] || rm $name.osm.pbf
  osrm-partition $name.osrm
  osrm-customize $name.osrm
  cp regions $name.regions
  if cmp -s map.regions regions && cmp -s foot.regions regions; then
    rm -f map.osm.pbf
  fi
fi
exec osrm-routed --algorithm mld $name.osrm
