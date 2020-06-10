def dict_deep_merge(a, b, path=None):
    """merges dictionary b into a recursively"""
    if path is None:
        path = []
    for key in b:
        if key in a:
            a_is_dict = isinstance(a[key], dict)
            b_is_dict = isinstance(b[key], dict)
            if a_is_dict and b_is_dict:
                dict_deep_merge(a[key], b[key], path + [str(key)])
            elif not a_is_dict and not b_is_dict:
                a[key] = b[key]
            else:
                raise TypeError('Conflict at %s' % '.'.join(path + [str(key)]))
        else:
            a[key] = b[key]
    return a
