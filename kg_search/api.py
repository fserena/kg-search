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

from flask import request
from flask.json import jsonify

from kg_search.search import search_seeds_from_image, search_seeds
from kg_search import app, cache

__author__ = 'Fernando Serena'


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
        limit = request.args.get('limit', 200)
        entities = {}
        if img is not None:
            gen = search_seeds_from_image(img, types=types)
        else:
            gen = search_seeds(q, types=types, count=limit)
        for types, q, db, wiki, name in gen:
            for t in types:
                if t not in entities:
                    entities[t] = []
                entities[t].append({'entity': q, 'name': name, 'dbpedia': db, 'wikipedia': wiki})
        return jsonify(entities)
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5015, use_reloader=False, debug=False, threaded=True)
