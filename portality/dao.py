import os, json, UserDict, requests, uuid
from copy import deepcopy
from datetime import datetime

try:
    from portality.core import app
except:
    # use DAO outside of a flask app
    pass

'''
All models in models.py should inherit this DomainObject to know how to save themselves in the index and so on.
You can overwrite and add to the DomainObject functions as required. See models.py for some examples.
'''
    
    
class DomainObject(UserDict.IterableUserDict):
    __type__ = None # set the type on the model that inherits this
    
    # these can be set manually on inheriting models to directly control them
    HOST = None
    INDEX = None

    def __init__(self, **kwargs):
        if '_source' in kwargs:
            self.data = dict(kwargs['_source'])
            self.meta = dict(kwargs)
            del self.meta['_source']
        elif 'data' in kwargs:
            self.data = dict(kwargs['data'])
        else:
            self.data = dict(kwargs)

    # set index connections data
    @classmethod
    def target(cls,layer='type'):
        if cls.HOST is not None:
            t = cls.HOST
        else:
            try:
                t = str(app.config['ELASTIC_SEARCH_HOST'])
            except:
                t = 'http://localhost:9200'

        t = t.rstrip('/') + '/'
        if layer == 'host': return t
        
        if cls.INDEX is not None:
            t += cls.INDEX
        else:
            try:
                t += str(app.config['ELASTIC_SEARCH_DB'])
            except:
                t += 'unknown'

        t += '/'
        if layer == 'index': return t

        return t + cls.__type__ + '/'

            
    @classmethod
    def makeid(cls):
        '''Create a new id for data object
        overwrite this in specific model types if required'''
        return uuid.uuid4().hex

    @property
    def id(self):
        return self.data.get('id', None)
        
    @property
    def version(self):
        return self.meta.get('_version', None)

    @property
    def json(self):
        return json.dumps(self.data)

    def save(self):
        if 'id' in self.data:
            id_ = self.data['id'].strip()
        else:
            id_ = self.makeid()
            self.data['id'] = id_
        
        self.data['last_updated'] = datetime.now().strftime("%Y-%m-%d %H%M")

        if 'created_date' not in self.data:
            self.data['created_date'] = datetime.now().strftime("%Y-%m-%d %H%M")
            
        r = requests.post(self.target() + self.data['id'], data=json.dumps(self.data))

    def save_from_form(self,request):
        newdata = request.json if request.json else request.values
        for k, v in newdata.items():
            if k not in ['submit']:
                self.data[k] = v
        self.save()

    @classmethod
    def bulk(cls, bibjson_list, idkey='id', refresh=False):
        data = ''
        for r in bibjson_list:
            if idkey not in r:
                r[idkey] = cls.makeid()
            data += json.dumps( {'index':{'_id':r[idkey]}} ) + '\n'
            data += json.dumps( r ) + '\n'
        r = requests.post(cls.target() + '_bulk', data=data)
        if refresh: cls.refresh()
        return r.json()


    @classmethod
    def refresh(cls):
        r = requests.post(cls.target(layer='index') + '_refresh')
        return r.json()


    @classmethod
    def pull(cls, id_):
        '''Retrieve object by id.'''
        if id_ is None:
            return None
        try:
            out = requests.get(cls.target() + id_)
            if out.status_code == 404:
                return None
            else:
                return cls(**out.json())
        except:
            return None

    @classmethod
    def pull_by_key(cls,key,value):
        try:
            res = cls.query(q={"query":{"term":{key+app.config['FACET_FIELD']:value}}})
        except:
            res = cls.query(q={"query":{"term":{key:value}}})
        if res.get('hits',{}).get('total',0) == 1:
            return cls.pull( res['hits']['hits'][0]['_source']['id'] )
        else:
            return None
    
    @classmethod
    def term(cls, field, value, one_answer=False):
        query = {
            "query" : {
                "term" : {field : value}
            }
        }
        result = cls.query(q=query)
        objects = [i.get("_source", {}) for i in result.get('hits', {}).get('hits', [])]
        if one_answer:
            return objects[0]
        return objects

    @classmethod
    def keys(cls,mapping=False,prefix=''):
        # return a sorted list of all the keys in the index
        if not mapping:
            mapping = cls.query(endpoint='_mapping')[cls.__type__]['properties']
        keys = []
        for item in mapping:
            if mapping[item].has_key('fields'):
                for item in mapping[item]['fields'].keys():
                    keys.append(prefix + item)
            else:
                keys = keys + cls.keys(mapping=mapping[item]['properties'],prefix=prefix+item+'.')
        keys.sort()
        return keys

    
    @classmethod
    def put_mapping(cls, mapping):
        im = cls.target() + '_mapping'
        exists = requests.get(im)
        if exists.status_code != 200:
            ri = requests.post(cls.target(layer="index"))
        r = requests.put(im, json.dumps(mapping))


    @classmethod
    def mapping(cls):
        return cls.query(endpoint='_mapping')[cls.__type__]

    @classmethod
    def query(cls, recid='', endpoint='_search', q='', terms=None, facets=None, **kwargs):
        '''Perform a query on backend.

        :param recid: needed if endpoint is about a record, e.g. mlt
        :param endpoint: default is _search, but could be _mapping, _mlt, _flt etc.
        :param q: maps to query_string parameter if string, or query dict if dict.
        :param terms: dictionary of terms to filter on. values should be lists. 
        :param facets: dict of facets to return from the query.
        :param kwargs: any keyword args as per
            http://www.elasticsearch.org/guide/reference/api/search/uri-request.html
        '''
        if recid and not recid.endswith('/'): recid += '/'
        if isinstance(q,dict):
            query = q
            if 'bool' not in query['query']:
                boolean = {'bool':{'must': [] }}
                boolean['bool']['must'].append( query['query'] )
                query['query'] = boolean
            if 'must' not in query['query']['bool']:
                query['query']['bool']['must'] = []
        elif q:
            query = {
                'query': {
                    'bool': {
                        'must': [
                            {'query_string': { 'query': q }}
                        ]
                    }
                }
            }
        else:
            query = {
                'query': {
                    'bool': {
                        'must': [
                            {'match_all': {}}
                        ]
                    }
                }
            }

        if facets:
            if 'facets' not in query:
                query['facets'] = {}
            for k, v in facets.items():
                query['facets'][k] = {"terms":v}

        if terms:
            boolean = {'must': [] }
            for term in terms:
                if not isinstance(terms[term],list): terms[term] = [terms[term]]
                for val in terms[term]:
                    obj = {'term': {}}
                    obj['term'][ term ] = val
                    boolean['must'].append(obj)
            if q and not isinstance(q,dict):
                boolean['must'].append( {'query_string': { 'query': q } } )
            elif q and 'query' in q:
                boolean['must'].append( query['query'] )
            query['query'] = {'bool': boolean}

        for k,v in kwargs.items():
            if k == '_from':
                query['from'] = v
            else:
                query[k] = v

        if endpoint in ['_mapping']:
            r = requests.get(cls.target() + endpoint)
        else:
            r = requests.post(cls.target() + recid + endpoint, data=json.dumps(query))
        return r.json()


    def delete(self):        
        r = requests.delete(self.target() + self.id)

    @classmethod
    def delete_type(cls):
        r = requests.delete(cls.target())

    @classmethod
    def delete_index(cls):
        r = requests.delete(cls.target(layer='index'))
    
    @classmethod
    def iterate(cls, q, page_size=1000, limit=None):
        q["size"] = page_size
        q["from"] = 0
        counter = 0
        while True:
            # apply the limit
            if limit is not None and counter >= limit:
                break
            
            res = cls.query(q=q)
            rs = [r.get("_source") for r in res.get("hits", {}).get("hits", [])]
            if len(rs) == 0:
                break
            for r in rs:
                # apply the limit (again)
                if limit is not None and counter >= limit:
                    break
                counter += 1
                yield r
            q["from"] += page_size   
    
    @classmethod
    def iterall(cls, page_size=1000, limit=None):
        return cls.iterate(deepcopy(all_query), page_size, limit)

########################################################################
## Some useful ES queries
########################################################################

all_query = { 
    "query" : { 
        "match_all" : { }
    }
}
    

