#!/bin/bash
cd ~/volttron/scripts/scalability-testing/
fab build_configs
cd ..
python install_master_driver_configs.py scalability-testing/configs
cd -
fab deploy_device_configs
sshpass -p 'passwordToDevicesVM' ssh -o StrictHostKeyChecking=no  deviceVMUser@deviceVMIP '~/startModbusDevices.sh; echo Devices started;'
cd ~/volttron/services/core/MasterDriverAgent 
python -m master_driver.agent
sshpass -p 'passwordToDevicesVM' ssh -o StrictHostKeyChecking=no  deviceVMUser@deviceVMIP 'killall python; echo devices killed;'
