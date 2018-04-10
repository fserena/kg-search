FROM fserena/python-base
LABEL maintainer=kudhmud@gmail.com

RUN /root/.env/bin/pip install git+https://github.com/fserena/kg-search.git
