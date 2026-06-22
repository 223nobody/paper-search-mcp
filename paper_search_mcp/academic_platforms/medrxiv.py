from .biorxiv import BioRxivBaseSearcher


class MedRxivSearcher(BioRxivBaseSearcher):
    """Searcher for medRxiv papers."""

    _API_DETAIL = "medrxiv"
    _HOST = "www.medrxiv.org"
    _SOURCE = "medrxiv"
