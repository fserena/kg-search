"""
#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=#
  Ontology Engineering Group
        http://www.oeg-upm.net/
#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=#
  Copyright (C) 2016 Ontology Engineering Group.
#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=#
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

            http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=#
"""

import traceback

from flask import Flask, request
from flask.json import jsonify
from flask_cache import Cache

from kg_search.search import search_seeds_from_image, search_seeds

__author__ = 'Fernando Serena'

app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'filesystem', 'CACHE_DIR': 'cache'})


def make_cache_key(*args, **kwargs):
    path = request.path
    qargs = dict(request.args.items())
    args = ''.join(['{}{}'.format(k, qargs[k]) for k in sorted(qargs.keys())])
    return (path + args).encode('utf-8')


@app.route('/search')
@cache.cached(timeout=3600, key_prefix=make_cache_key)
def search():
    try:
        q = request.args.get('q')
        img = request.args.get('img')
        types = request.args.getlist('types')
        entities = {}
        if img is not None:
            gen = search_seeds_from_image(img, types=types)
        else:
            gen = search_seeds(q, types=types)
        for types, q, name in gen:
            for t in types:
                if t not in entities:
                    entities[t] = []
                entities[t].append({'entity': q, 'name': name})
        return jsonify(entities)
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5015, use_reloader=False, debug=False, threaded=True)
