import json
import traceback
import urlparse
from urllib import quote, unquote
import shelve
import os
import requests
import sys
from SPARQLWrapper import JSON
from SPARQLWrapper import SPARQLWrapper
from StringIO import StringIO
from rdflib import Graph, Namespace
from concurrent.futures import ThreadPoolExecutor, wait

from kg_search.ld import ld_triples
from kg_search import kg_cache, wd_cache, app
from difflib import SequenceMatcher

SCHEMA = Namespace('http://schema.org/')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    sys.exit(-1)

wiki_d = shelve.open('wiki-entities')

pool = ThreadPoolExecutor()


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


@wd_cache.memoize(3600)
def search_wiki_entity(wiki):
    sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
    sparql.setReturnFormat(JSON)

    wiki = str(wiki)
    # if wiki not in wiki_d:
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

        # wiki_d[wiki] = entity

    return entity
    # return wiki_d[wiki]


def search_dbpedia_uri(wiki):
    wiki = str(wiki)
    parse = urlparse.urlparse(wiki)
    dbpedia_path = parse.path.replace('wiki', 'resource')
    return 'http://dbpedia.org' + dbpedia_path


@wd_cache.memoize(3600)
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


def enrich_wiki_entry(wiki, name, types):
    entity = search_wiki_entity(wiki)
    dbpedia = search_dbpedia_uri(wiki)
    # enrich_types = types
    # if len(types) == 1 and 'Thing' in types:
    #     dbpedia_types = set(search_types_in_dbpedia(dbpedia))
    #     enrich_types = dbpedia_types.union(set(types))

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


@kg_cache.memoize(3600)
def _kg_request(q, types=None, count=None):
    print u'querying "{}" with types {} [max {}] ...'.format(q, types, count)
    kg_request_url = u'https://kgsearch.googleapis.com/v1/entities:search?query={}&key={}&indent=True'.format(
        q, GOOGLE_API_KEY, count)

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

    score_th = avg_score    #  * 0.5 + max_score * 0.5
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
            if ty != 'Thing' and score > 0.5 / ref_score and similar(source_q, name) > 0.5 / ref_score:
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


def search_entities(q, **kwargs):
    kg_results = _kg_search(q, **kwargs)

    results = []
    futures = []

    for wiki, res in kg_results.items():
        types = res['types']
        name = res['name']
        score = res['score']
        future = pool.submit(enrich_wiki_entry, wiki=wiki, name=name, types=types)
        futures.append(future)
        results.append((future, wiki, name, score))

    with app.app_context():
        wait(futures)
        for future, wiki, name, score in results:
            types, entity, dbpedia = future.result()
            yield (types, entity, dbpedia, wiki, name, score)


@kg_cache.memoize(3600)
def search_seeds_from_image(img, types=None, count=None):
    r = requests.post(
        'https://vision.googleapis.com/v1/images:annotate?key={}'.format(GOOGLE_API_KEY),
        data=json.dumps({
            "requests": [
                {
                    "image": {
                        "source": {
                            "imageUri": img
                        }
                    },
                    "features": [
                        {
                            "type": "WEB_DETECTION"
                        }
                    ]
                }
            ]
        }))

    # if count is None:
    #     count = 1

    if r.status_code == 200:
        data = r.json()
        try:
            descriptions = map(lambda x: x['description'], data['responses'][0]['webDetection']['webEntities'])
            for d in descriptions:
                for seed_tuple in search_seeds(d, types=types, count=count):
                    yield seed_tuple
        except:
            pass


def search_seeds(search, types=None, **kwargs):
    if not types:
        for res in search_entities(search, **kwargs):
            yield res
    else:
        for type in types:
            for res in search_entities(search, types=[type], **kwargs):
                yield res
