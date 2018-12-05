#!/bin/bash
# Starts specified number of modbus devices in the background
for port in {50200..50299}
do
    $HOME/volttron/env/bin/python $HOME/volttron/scripts/scalability-testing/virtual-drivers/modbus.py $HOME/scalability-configurations/catalyst371.csv 172.20.214.72 --port=$port --no-daemon > /dev/null 2>&1 &
done
