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

from flask import request, redirect
from flask.json import jsonify
from werkzeug.utils import secure_filename

from kg_search import app, cache
from kg_search.search import search_seeds_from_image, search_seeds_from_text, search_seeds_from_url

__author__ = 'Fernando Serena'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}


def make_cache_key(*args, **kwargs):
    path = request.path
    qargs = dict(request.args.items())
    args = ''.join(['{}{}'.format(k, qargs[k]) for k in sorted(qargs.keys())])
    return (path + args).encode('utf-8')


def are_equal(a, b):
    return a.split('/')[-1] == b.split('/')[-1]


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/search', methods=['GET', 'POST'])
@cache.cached(timeout=3600, key_prefix=make_cache_key, unless=lambda: request.method == 'POST')
def search():
    img = None
    raw = False
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        # if user does not select file, browser also
        # submit an empty part without filename
        if file.filename == '':
            return redirect(request.url)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            # return redirect(url_for('uploaded_file',
            #                         filename=filename))
            img = file

    if img is None:
        img = request.args.get('img')
        q = request.args.get('q')
        url = request.args.get('url')

    raw = request.args.get('raw', None)
    if raw is not None:
        raw = True

    try:
        types = request.args.getlist('types')
        limit = request.args.get('limit', None)
        if limit is not None:
            limit = int(limit)
        best = request.args.get('best', None)
        if best is not None:
            best = True

        entities = {}
        if img is not None:
            gen = search_seeds_from_image(img, types=types, count=limit, raw=raw)
            if raw:
                return jsonify(list(gen))
        elif url is not None:
            gen = search_seeds_from_url(url, types=types, count=limit)
        else:
            gen = search_seeds_from_text(q, types=types, count=limit)

        best_score = 0.0
        n = 0
        for types, q, db, wiki, name, score in sorted(list(gen), key=lambda x: x[5], reverse=True):
            if score < 0.1:
                continue
            if best and (score - best_score) > 0.05:
                entities = {}
                best_score = score
            if best and abs(score - best_score) > 0.05:
                continue
            for t in types:
                if t not in entities:
                    entities[t] = []
                s_dict = {'wikidata': q, 'name': name, 'dbpedia': db, 'wikipedia': wiki, 'score': score}
                if not any(filter(
                        lambda x: are_equal(x['wikipedia'], s_dict['wikipedia']) or (
                                        x['wikidata'] is not None and x['wikidata'] == s_dict['wikidata']),
                        entities[t])):
                    entities[t].append(s_dict)

            n += 1
            if n == limit:
                break
        return jsonify(entities)
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5015, use_reloader=False, debug=False, threaded=True)
