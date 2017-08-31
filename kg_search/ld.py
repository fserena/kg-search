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
from datetime import datetime
from rfc822 import mktime_tz, parsedate_tz

import isodate
import shortuuid
from pyld import jsonld
from rdflib import URIRef, XSD, RDFS, Graph
from rdflib.term import Literal, BNode

__author__ = 'Fernando Serena'


def ld_triples(ld, g=None):
    bid_map = {}

    def parse_term(term):
        if term['type'] == 'IRI':
            return URIRef(term['value'])
        elif term['type'] == 'literal':
            datatype = URIRef(term.get('datatype', None))
            if datatype == XSD.dateTime:
                try:
                    term['value'] = float(term['value'])
                    term['value'] = datetime.utcfromtimestamp(term['value'])
                except:
                    try:
                        term['value'] = isodate.parse_datetime(term['value'])
                    except:
                        timestamp = mktime_tz(parsedate_tz(term['value']))
                        term['value'] = datetime.fromtimestamp(timestamp)
            if datatype == RDFS.Literal:
                datatype = None
                try:
                    term['value'] = float(term['value'])
                except:
                    pass
            return Literal(term['value'], datatype=datatype)
        else:
            bid = term['value'].split(':')[1]
            if bid not in bid_map:
                bid_map[bid] = shortuuid.uuid()
            return BNode(bid_map[bid])

    if g is None:
        g = Graph()
    norm = jsonld.normalize(ld)
    def_graph = norm.get('@default', [])
    for triple in def_graph:
        subject = parse_term(triple['subject'])
        predicate = parse_term(triple['predicate'])
        if not predicate.startswith('http'):
            continue
        object = parse_term(triple['object'])
        g.add((subject, predicate, object))

    return g
