import json
import urlparse
from urllib import quote, unquote
import shelve
import os
import requests
import sys
from SPARQLWrapper import JSON
from SPARQLWrapper import SPARQLWrapper
from rdflib import Graph, Namespace

from kg_search.ld import ld_triples

SCHEMA = Namespace('http://schema.org/')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    sys.exit(-1)

sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
sparql.setReturnFormat(JSON)

wiki_d = shelve.open('wiki-entities')


def find_wiki_entity(wiki):
    wiki = str(wiki)
    if wiki not in wiki_d:
        sparql.setQuery("""
           prefix schema: <http://schema.org/>
           SELECT * WHERE {
               <%s> schema:about ?item .
           }
       """ % str(wiki))

        entity = None
        try:
            results = sparql.query().convert()

            for result in results["results"]["bindings"]:
                entity = result["item"]["value"].replace('http://www.wikidata.org/entity/', '')
                break
        except Exception:
            pass

        wiki_d[wiki] = entity

    return wiki_d[wiki]


def iriToUri(iri):
    parts = urlparse.urlparse(iri)
    return urlparse.urlunparse(
        part.encode('utf-8') if parti == 1 else quote(part.encode('utf-8'))
        for parti, part in enumerate(parts)
    )


def search_entities(q, types=None, count=200):
    kg_request_url = 'https://kgsearch.googleapis.com/v1/entities:search?query={}&key={}&indent=True&limit={}'.format(
        q, GOOGLE_API_KEY, count)

    if isinstance(types, list):
        types = ','.join(types)
        kg_request_url += '&types={}'.format(types)

    kg_response = requests.get(kg_request_url)

    kg = Graph()
    kg.bind('schema', SCHEMA)
    ld_triples(kg_response.json(), kg)

    print kg.serialize(format='turtle')

    kg_query_result = kg.query("""
            SELECT DISTINCT (MIN(?score) as ?min) (MAX(?score) as ?max) (AVG(?score) as ?avg) WHERE {
               [] <http://schema.googleapis.com/resultScore> ?score
            }
        """)

    try:
        max_min_score = list(kg_query_result).pop()
        max_score, min_score, avg_score = max_min_score.max.toPython(), \
                                          max_min_score.min.toPython(), \
                                          max_min_score.avg.toPython()

    except:
        max_score, min_score, avg_score = 0, 0, 0

    score_th = avg_score * 0.1 + min_score * 0.9
    print max_score, min_score, avg_score, score_th

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
    for kgr in kg_query_result:
        if kgr.score.toPython() >= score_th:
            print kgr.score, kgr.wiki
            wiki_uri = iriToUri(kgr.wiki)
            wiki_uri = unquote(wiki_uri).decode('utf8')
            if (wiki_uri, kgr.name) not in res_dict:
                res_dict[(wiki_uri, kgr.name)] = set([])
            res_dict[(wiki_uri, kgr.name)].add(kg.qname(kgr.type).split(':')[1])

    for (wiki, name), types in res_dict.items():
        entity = find_wiki_entity(wiki)
        print wiki, types, entity
        yield (types, entity, name)


def search_seeds_from_image(img, types=None):
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

    if r.status_code == 200:
        data = r.json()
        try:
            descriptions = map(lambda x: x['description'], data['responses'][0]['webDetection']['webEntities'])
            for d in descriptions:
                for seed_tuple in search_seeds(d, types=types, count=1):
                    yield seed_tuple
        except:
            pass


def search_seeds(search, types=None, count=200):
    if not types:
        for entity_types, q, name in search_entities(search, count=count):
            yield list(entity_types), q, name
    else:
        for type in types:
            for entity_types, q, name in search_entities(search, types=[type], count=count):
                yield list(entity_types), q, name
