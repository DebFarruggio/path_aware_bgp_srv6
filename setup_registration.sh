#!/bin/bash

echo "Starting collecting segments from the ASes..."

kathara exec as1 -- python3 shared/phase_1_registration/registration_client.py &
kathara exec as3 -- python3 shared/phase_1_registration/registration_client.py &
kathara exec as4 -- python3 shared/phase_1_registration/registration_client.py &
kathara exec as5 -- python3 shared/phase_1_registration/registration_client.py &
kathara exec as6 -- python3 shared/phase_1_registration/registration_client.py &

wait
echo "All collection processes are started"
