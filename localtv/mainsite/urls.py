from django.conf.urls.defaults import patterns

urlpatterns = patterns(
    "",
    (r'^$',
     'django.views.generic.simple.direct_to_template',
     {'template': 'localtv/mainsite/index.html'},
     'localtv_mainsite_index'))