#!/bin/sh
echo "Starting kg-search..."

/root/.env/bin/pip install --upgrade pip
/root/.env/bin/pip install --upgrade git+https://github.com/fserena/kg-search.git
/root/.env/bin/kg-search &
