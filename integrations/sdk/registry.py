"""Provider registry -- keyed by slug."""
_REGISTRY = {}


def register(provider_cls):
    """Decorator: register a provider class. Use as @register on the class."""
    instance = provider_cls()
    if not instance.slug:
        raise ValueError(f'{provider_cls.__name__} missing slug')
    _REGISTRY[instance.slug] = instance
    return provider_cls


def get(slug):
    return _REGISTRY.get(slug)


def all_providers():
    return dict(_REGISTRY)


def by_category(category):
    return {s: p for s, p in _REGISTRY.items() if p.category == category}
