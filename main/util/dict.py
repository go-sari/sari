from pprint import pformat

from dictdiffer import diff


def assert_dict_equals(actual: dict, expected: dict):
    differences = list(diff(actual, expected))
    if differences:
        # To avoid truncation of AssertionError message
        raise AssertionError("Dict diff:\n{}".format(pformat(differences)))


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
