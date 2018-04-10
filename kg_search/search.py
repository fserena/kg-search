import base64
import json
import traceback
import urllib
import urlparse
from urllib import quote, unquote
import shelve
import os
import requests
import sys
from SPARQLWrapper import JSON
from SPARQLWrapper import SPARQLWrapper
from StringIO import StringIO
from rdflib import Graph, Namespace, URIRef
from concurrent.futures import ThreadPoolExecutor, wait

from kg_search.ld import ld_triples
from kg_search import kg_cache, wd_cache, wp_cache, dn_cache, app
from difflib import SequenceMatcher
import wikipedia

SCHEMA = Namespace('http://schema.org/')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
DANDELION_API_KEY = os.environ.get('DANDELION_API_KEY')
if not GOOGLE_API_KEY:
    sys.exit(-1)

wiki_d = shelve.open('wiki-entities')

pool = ThreadPoolExecutor()


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


@wd_cache.memoize(864000)
def search_wiki_entity(wiki):
    sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
    sparql.setReturnFormat(JSON)

    try:
        wiki = urllib.unquote(str(wiki))
    except ValueError:
        wiki = str(wiki
                   )
    wiki = wiki.replace("'", '%27')
    wiki = wiki.replace("%28", '(')
    wiki = wiki.replace("%29", ')')
    wiki = wiki.replace("%3A", ":")
    wiki = wiki.replace("%2C", ",")
    sparql.setQuery("""
       prefix schema: <http://schema.org/>
       SELECT * WHERE {
           <%s> schema:about ?item .
       }
   """ % wiki)

    entity = None
    try:
        results = sparql.query().convert()

        for result in results["results"]["bindings"]:
            entity = result["item"]["value"].replace('http://www.wikidata.org/entity/', '')
            if entity:
                print u'found {} for {}'.format(entity, wiki)
            break
    except Exception:
        pass

    if entity is None and 'https://' not in wiki:
        return search_wiki_entity(wiki.replace('http:', 'https:'))

    return entity


def search_dbpedia_uri(wiki):
    wiki = str(wiki)
    parse = urlparse.urlparse(wiki)
    dbpedia_path = parse.path.replace('wiki', 'resource')
    return 'http://dbpedia.org' + dbpedia_path


@dn_cache.memoize(864000)
def recognize_entities(q=None, url=None):
    request_url = u'https://api.dandelion.eu/datatxt/nex/v1?token={}&'.format(DANDELION_API_KEY)
    results = {}
    if url is not None:
        request_url += u'url={}'.format(url)
    else:
        request_url += u'text={}'.format(q)

    response = requests.get(request_url)

    if response.status_code == 200:
        data = response.json()
        for an in data['annotations']:
            wiki = an['uri']
            if an['confidence'] > 0.5:
                r = {
                    'name': an.get('title'),
                    'score': an.get('confidence')
                }

                results[wiki] = r
    return results


@wd_cache.memoize(864000)
def search_types_in_dbpedia(dbpedia_uri):
    sparql = SPARQLWrapper("http://dbpedia.org/sparql")
    sparql.setReturnFormat(JSON)

    sparql.setQuery("""           
           SELECT * WHERE {
               <%s> rdf:type ?type .
           }
       """ % dbpedia_uri)

    types = set()

    try:
        results = sparql.query().convert()

        for result in results["results"]["bindings"]:
            ty = result["type"]["value"]
            if ty.startswith('http://schema.org/'):
                types.add(str(ty).replace('http://schema.org/', ''))
    except Exception:
        pass

    return types


@wd_cache.memoize(864000)
def search_types_in_wikidata(entity):
    sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
    sparql.setReturnFormat(JSON)

    sparql.setQuery("""           
           SELECT DISTINCT ?wd WHERE {
               wd:%s wdt:P31/wdt:P279* ?super .
               ?super wdt:P1709 ?wd
           }
       """ % entity)

    types = set()

    try:
        results = sparql.query().convert()

        for result in results["results"]["bindings"]:
            s = result["wd"]["value"]
            if s.startswith(SCHEMA):
                types.add(s.replace(SCHEMA, ''))
    except Exception:
        pass

    return types


@wd_cache.memoize(8640000)
def enrich_wiki_entry(wiki, name, types):
    entity = search_wiki_entity(wiki)
    dbpedia = search_dbpedia_uri(wiki)
    types = search_types_in_wikidata(entity).union(types)

    return types, entity, dbpedia


def iriToUri(iri):
    parts = urlparse.urlparse(iri)
    return urlparse.urlunparse(
        part.encode('utf-8') if parti == 1 else quote(part.encode('utf-8'))
        for parti, part in enumerate(parts)
    )


def median(lst):
    n = len(lst)
    if n < 1:
        return None
    if n % 2 == 1:
        return sorted(lst)[n // 2]
    else:
        return sum(sorted(lst)[n // 2 - 1:n // 2 + 1]) / 2.0


@kg_cache.memoize(864000)
def _kg_request(q, types=None, count=None):
    print u'querying "{}" with types {} [max {}] ...'.format(q, types, count)
    kg_request_url = u'https://kgsearch.googleapis.com/v1/entities:search?query={}&key={}&indent=True'.format(
        q, GOOGLE_API_KEY)

    if count is not None:
        kg_request_url += '&limit={}'.format(count)

    if isinstance(types, list):
        types = ','.join(types)
        kg_request_url += '&types={}'.format(types)

    kg_response = requests.get(kg_request_url)

    kg = Graph()
    kg.bind('schema', SCHEMA)
    ld_triples(kg_response.json(), kg)
    # print kg.serialize(format='turtle')
    return kg.serialize(format='turtle')


def _kg_search(q, types=None, count=None, trace=None, source_q=None, ref_score=1.0):
    if trace is None:
        trace = []

    if source_q is None:
        source_q = q

    if (q, types) not in trace:
        trace.append((q, types))
    else:
        return {}

    kg = Graph()
    kg.parse(StringIO(_kg_request(q, types=types, count=count)), format='turtle')
    kg_query_result = kg.query("""
                        SELECT ?score WHERE {
                           [] <http://schema.googleapis.com/resultScore> ?score
                        }
                    """)

    scores = map(lambda x: x.score.toPython(), kg_query_result)
    if not scores:
        return {}

    kgr_max_score = max(scores)

    try:
        scores = map(lambda x: x / kgr_max_score, scores)
        min_score = min(scores)
        avg_score = sum(scores) / len(scores)
        max_score = max(scores)
    except:
        max_score, min_score, avg_score = 0, 0, 0

    score_th = avg_score  # * 0.5 + max_score * 0.5
    deep_th = avg_score * 0.1 + max_score * 0.9
    print max_score, min_score, avg_score, score_th, deep_th

    kg_query_result = kg.query("""
                SELECT DISTINCT ?wiki ?type ?score ?name WHERE {
                   [] <http://schema.org/result> [
                         a ?type ;
                         <http://schema.googleapis.com/detailedDescription> [
                            <http://schema.org/url> ?wiki
                         ] ;
                         <http://schema.org/name> ?name
                      ] ;
                      <http://schema.googleapis.com/resultScore> ?score
                }
            """)

    res_dict = {}
    types_score = {}
    for kgr in kg_query_result:
        kgr_score = kgr.score.toPython() / kgr_max_score
        if kgr_score >= score_th:
            wiki_uri = iriToUri(kgr.wiki)
            wiki_uri = unquote(wiki_uri).decode('utf8')
            ty = kg.qname(kgr.type).split(':')[1]

            if wiki_uri not in res_dict:
                res_dict[wiki_uri] = {'types': set(), 'name': kgr.name.toPython(), 'score': kgr_score / max_score}
            res_dict[wiki_uri]['types'].add(ty)

    for wiki, res in res_dict.items():
        types = res['types']
        name = res['name']
        score = res['score']
        try:
            print u'{} {} {}'.format(wiki, name, score)
        except:
            pass

        if len(types) == 1 and 'Thing' in types:
            dbpedia = search_dbpedia_uri(wiki)
            dbpedia_types = set(search_types_in_dbpedia(dbpedia))
            enrich_types = dbpedia_types.union(set(types))
            res_dict[wiki]['types'] = enrich_types

        for ty in res['types']:
            if ty != 'Thing' and score > 0.5 / ref_score and similar(source_q, name) > 0.5:
                if ty not in types_score:
                    types_score[ty] = set()
                types_score[ty].add((score, name))

    for ty, pairs in types_score.items():
        try:
            for score, name in pairs:
                if (name, [ty]) not in trace:
                    more = _kg_search(name, types=[ty], trace=trace, source_q=q, ref_score=score)
                    for wiki in more:
                        if wiki not in res_dict:
                            res_dict[wiki] = more[wiki]
        except Exception:
            traceback.print_exc()

    return res_dict


@kg_cache.memoize(864000)
def kg_search(q, **kwargs):
    return _kg_search(q, **kwargs)


def search_entities(q, **kwargs):
    kg_results = _kg_search(q, **kwargs)
    # wiki_entities = recognize_entities(q)

    results = []
    futures = []

    for wiki, res in kg_results.items():
        types = res['types']
        name = res['name']
        score = res['score']
        future = pool.submit(enrich_wiki_entry, wiki=wiki, name=name, types=types)
        futures.append(future)
        results.append((future, wiki, name, score))

    # for wiki, res in wiki_entities.items():
    #     types = res['types']
    #     name = res['name']
    #     score = res['score']
    #     future = pool.submit(enrich_wiki_entry, wiki=wiki, name=name, types=types)
    #     futures.append(future)
    #     results.append((future, wiki, name, score))

    with app.app_context():
        wait(futures)
        for future, wiki, name, score in results:
            types, entity, dbpedia = future.result()
            yield (types, entity, dbpedia, wiki, name, score)


@wp_cache.memoize(864000)
def search_types(search):
    def page_fields(x):
        try:
            page = wikipedia.page(x)
            return page.title, page.url
        except Exception:
            pass

    q_types = {}
    try:
        try:
            w_search_pages = [page_fields(x) for x in wikipedia.search(search, results=1)]
        except wikipedia.exceptions.DisambiguationError as e:
            w_search_pages = [page_fields(x) for x in e.options if 'disambiguation' not in x]
            w_search_pages = filter(lambda x: x, w_search_pages)

        for title, url in w_search_pages:
            w_dbpedia = search_dbpedia_uri(url)
            dbpedia_types = search_types_in_dbpedia(w_dbpedia)
            q_types[title] = dbpedia_types
            print u'{} types: {}'.format(title, q_types[title])
    except:
        traceback.print_exc()
        pass

    return q_types


@kg_cache.memoize(86400)
def search_seeds_from_image(img, types=None, count=None, raw=False):

    if isinstance(img, URIRef):
        image = {
            "source": {
                "imageUri": str(img)
            }
        }
    else:
        image = {
            "content": base64.b64encode(img.read())
        }

    print GOOGLE_API_KEY
    r = requests.post(
        'https://vision.googleapis.com/v1/images:annotate?key={}'.format(GOOGLE_API_KEY),
        data=json.dumps({
            "requests": [
                {
                    "image": image,
                    "features": [
                        {
                            "type": "WEB_DETECTION"
                        }
                    ]
                }
            ]
        }))

    print r.status_code
    if r.status_code == 200:
        data = r.json()
        if types is None:
            types = []
        try:
            entities = data['responses'][0]['webDetection']['webEntities']
            mean_score = sum(map(lambda x: x.get('score'), entities)) / len(entities)

            descriptions = [x.get('description', None) for x in entities if x.get('score') >= mean_score]
            descriptions = filter(lambda x: x is not None, descriptions)

            if raw:
                for d in descriptions:
                    yield d
            else:
                desc_types = {}
                for desc in filter(lambda x: x, descriptions):
                    for q, d_types in search_types(desc.lower()).items():
                        if q not in desc_types:
                            r_types = set.intersection(d_types, types) if types else d_types
                            desc_types[q] = list(r_types)
                        else:
                            r_types = set(desc_types[q]).intersection(d_types) if types else set(desc_types[q]).union(d_types)
                            desc_types[q] = list(r_types)
                for d, found_types in desc_types.items():
                    for seed_tuple in search_seeds(d, types=list(set(types).union(found_types)), count=count):
                        yield seed_tuple
        except:
            pass


@kg_cache.memoize(86400)
def search_seeds_from_text(q, types=None, count=None):
    if types is None:
        types = []

    wiki_entities = recognize_entities(q)
    futures = []
    results = []

    for wiki, res in wiki_entities.items():
        name = res['name']
        score = res['score']
        future = pool.submit(enrich_wiki_entry, wiki=wiki, name=name, types=['Thing'])
        futures.append(future)
        results.append((future, wiki, name, score))

    all_q = {q}.union(map(lambda x: x['name'], wiki_entities.values()))

    for q in all_q:
        try:
            for seed_tuple in search_seeds(q.lower(), types=types, count=count):
                yield seed_tuple

            for q, found_types in search_types(q.lower()).items():
                for seed_tuple in search_seeds(q, types=list(set(types).union(found_types)), count=count):
                    yield seed_tuple
        except:
            pass

    with app.app_context():
        wait(futures)
        for future, wiki, name, score in results:
            types, entity, dbpedia = future.result()
            yield (types, entity, dbpedia, wiki, name, score)


@kg_cache.memoize(86400)
def search_seeds_from_url(url, types=None, count=None):
    if types is None:
        types = []

    wiki_entities = recognize_entities(url=url)
    futures = []
    results = []

    for wiki, res in wiki_entities.items():
        name = res['name']
        score = res['score']
        future = pool.submit(enrich_wiki_entry, wiki=wiki, name=name, types=['Thing'])
        futures.append(future)
        results.append((future, wiki, name, score))

    # all_q = set(map(lambda x: x['name'], wiki_entities.values()))
    #
    # for q in all_q:
    #     try:
    #         for seed_tuple in search_seeds(q.lower(), types=types, count=count):
    #             yield seed_tuple
    #
    #         for q, found_types in search_types(q.lower()).items():
    #             for seed_tuple in search_seeds(q, types=list(set(types).union(found_types)), count=count):
    #                 yield seed_tuple
    #     except:
    #         pass

    with app.app_context():
        wait(futures)
        for future, wiki, name, score in results:
            types, entity, dbpedia = future.result()
            yield (types, entity, dbpedia, wiki, name, score)


def search_seeds(search, types=None, **kwargs):
    if not types:
        for res in search_entities(search, **kwargs):
            yield res
    else:
        for type in types:
            for res in search_entities(search, types=[type], **kwargs):
                yield res
