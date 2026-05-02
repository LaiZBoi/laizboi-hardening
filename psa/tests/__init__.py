# PSA test package. See _base.py for shared helpers.
#
# Modules in this package are auto-discovered by Django's test runner. Run
# the whole PSA suite with `manage.py test psa`, or one module on its own:
#     manage.py test psa.tests.test_phase10_email
#     manage.py test psa.tests.test_legacy
#
# The split (v3.17.192) was driven by CI timeouts on the previous
# 5,465-line `psa/tests.py` — running 220+ cases in a single process
# routinely exceeded the 540 s wall-clock ceiling. Each module here is
# independently runnable so CI can shard the suite.
