from rdflib import Namespace


class FBONamespace:
    """
    Version-aware namespace helper for the FireBIM Ontology (FBO).
    Allows rule definitions to reference ontology terms safely.
    """

    def __init__(self, version="1.0", base_uri="http://example.org/fbo/"):

        self.version = version
        self.base_uri = base_uri

        self.uri = f"{base_uri}{version}/"
        self.ns = Namespace(self.uri)

    def __getattr__(self, name):

        return self.ns[name]

    def __str__(self):

        return self.uri
