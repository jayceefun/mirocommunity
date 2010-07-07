# Copyright 2009 - Participatory Culture Foundation
# 
# This file is part of Miro Community.
# 
# Miro Community is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
# 
# Miro Community is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with Miro Community.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import urllib

from django.contrib.auth.models import User
from django.contrib.syndication.feeds import Feed, FeedDoesNotExist, add_domain
from django.core.files.storage import default_storage
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponse, Http404
from django.utils import feedgenerator
from django.utils.encoding import iri_to_uri
from django.utils.translation import ugettext as _
from django.utils.tzinfo import FixedOffset

import haystack.forms
from tagging.models import Tag
import simplejson

from localtv import models
from localtv.templatetags.filters import simpletimesince

FLASH_ENCLOSURE_STATIC_LENGTH = 1

LOCALTV_FEED_LENGTH = 30

def feed_view(klass):
    def wrapper(request, *args):
        if len(args) == 0:
            args = [None]
        if args[0] == 'json/': # JSON feed
            json = True
        else:
            json = False
        args = args[1:]
        try:
            feed = klass(None, request, json=json).get_feed(*args)
        except FeedDoesNotExist:
            raise Http404
        else:
            mime_type = feed.mime_type
            if json and request.GET.get('jsoncallback'):
                output = '%s(%s);' % (
                    request.GET['jsoncallback'],
                    feed.writeString('utf8'))
                mime_type = 'text/javascript'
            else:
                output = feed.writeString('utf-8')
            return HttpResponse(output,
                                mimetype=mime_type)
    return wrapper

class ThumbnailFeedGenerator(feedgenerator.Atom1Feed):

    def root_attributes(self):
        attrs = feedgenerator.Atom1Feed.root_attributes(self)
        attrs['xmlns:media'] = 'http://search.yahoo.com/mrss/'
        return attrs

    def add_item_elements(self, handler, item):
        feedgenerator.Atom1Feed.add_item_elements(self, handler, item)
        if 'thumbnail' in item:
            handler.addQuickElement('media:thumbnail',
                                    attrs={'url': item['thumbnail']})
        if 'website_url' in item:
            handler.addQuickElement('link', attrs={
                    'rel': 'via',
                    'href': item['website_url']})
        if 'embed_code' in item:
            handler.startElement('media:player',
                                 {'url': item.get('website_url', '')})
            handler.characters(item['embed_code'])
            handler.endElement('media:player')

class JSONGenerator(feedgenerator.SyndicationFeed):
    mime_type = 'application/json'
    def write(self, outfile, encoding):
        json = {}
        self.add_root_elements(json)
        self.write_items(json)
        simplejson.dump(json, outfile, encoding=encoding)

    def add_root_elements(self, json):
        json['title'] = self.feed['title']
        json['link'] = self.feed['link']
        json['id'] = self.feed['id']
        json['updated'] = unicode(self.latest_post_date())

    def write_items(self, json):
        json['items'] = []
        for item in self.items:
            self.add_item_elements(json['items'], item)

    def add_item_elements(self, json_items, item):
        json_item = {}
        json_item['title'] = item['title']
        json_item['link'] = item['link']
        json_item['when'] = item['when']
        if item.get('pubdate'):
            json_item['pubdate'] = unicode(item['pubdate'])
        if item.get('description'):
            json_item['description'] = item['description']
        if item.get('enclosure'):
            enclosure = item['enclosure']
            json_item['enclosure'] = {
                'link': enclosure.url,
                'length': enclosure.length,
                'type': enclosure.mime_type}
        if item['categories']:
            json_item['categories'] = item['categories']
        if 'thumbnail' in item:
            json_item['thumbnail'] = item['thumbnail']
            json_item['thumbnails_resized'] = resized = []
            for size, url in item['thumbnails_resized'].items():
                resized.append({'width': size[0],
                                'height': size[1],
                                'url': url})
        if 'website_url' in item:
            json_item['website_url'] = item['website_url']
        if 'embed_code' in item:
            json_item['embed_code'] = item['embed_code']

        json_items.append(json_item)


class BaseVideosFeed(Feed):
    title_template = "localtv/feed/title.html"
    description_template = "localtv/feed/description.html"
    feed_type = ThumbnailFeedGenerator

    def __init__(self, *args, **kwargs):
        if 'json' in kwargs:
            if kwargs.pop('json'):
                self.feed_type = JSONGenerator
        Feed.__init__(self, *args, **kwargs)
        self.sitelocation = models.SiteLocation.objects.get(
            site=models.Site.objects.get_current())

    def item_pubdate(self, video):
        if video.status != models.VIDEO_STATUS_ACTIVE:
            return None
        return (video.when_published or video.when_approved).replace(
            tzinfo=FixedOffset(0))

    def item_guid(self, video):
        if video.guid:
            return video.guid
        return add_domain(video.site.domain, video.get_absolute_url())

    def item_link(self, video):
        return video.get_absolute_url()

    def item_extra_kwargs(self, item):
        kwargs = {
            'when': '%s %s ago' % (
                item.when_prefix(),
                simpletimesince(item.when()))
            }
        if item.website_url:
            kwargs['website_url'] = iri_to_uri(item.website_url)
        if item.has_thumbnail:
            if item.thumbnail_url:
                kwargs['thumbnail'] = iri_to_uri(item.thumbnail_url)
            else:
                default_url = default_storage.url(
                    item.get_resized_thumb_storage_path(375, 295))
                if not (default_url.startswith('http://') or
                        default_url.startswith('https://')):
                    default_url = 'http://%s%s' % (
                    self.sitelocation.site.domain, default_url)
                kwargs['thumbnail'] = default_url
            kwargs['thumbnails_resized'] = resized = {}
            for size in models.THUMB_SIZES:
                url = default_storage.url(
                    item.get_resized_thumb_storage_path(*size))
                if not (url.startswith('http://') or
                        url.startswith('http://')):
                    url = 'http://%s%s' % (
                        self.sitelocation.site.domain, url)
                resized[size] = url
        if item.embed_code:
            kwargs['embed_code'] = item.embed_code
        return kwargs

    def item_enclosure_url(self, video):
        if video.file_url:
            return video.file_url
        elif video.flash_enclosure_url:
            return video.flash_enclosure_url

    def item_enclosure_length(self, video):
        if video.file_url_length:
            return video.file_url_length
        else:
            return FLASH_ENCLOSURE_STATIC_LENGTH

    def item_enclosure_mime_type(self, video):
        if video.file_url_mimetype:
            return video.file_url_mimetype
        elif video.flash_enclosure_url:
            return 'application/x-shockwave-flash'
        else:
            return ""


class NewVideosFeed(BaseVideosFeed):
    def link(self):
        return reverse('localtv_list_new')

    def title(self):
        return "%s: %s" % (
            self.sitelocation.site.name, _('New Videos'))

    def items(self):
        videos = models.Video.objects.new(
            site=self.sitelocation.site,
            status=models.VIDEO_STATUS_ACTIVE)
        return videos[:LOCALTV_FEED_LENGTH]


class FeaturedVideosFeed(BaseVideosFeed):
    def link(self):
        return reverse('localtv_list_featured')

    def title(self):
        return "%s: %s" % (
            self.sitelocation.site.name, _('Featured Videos'))

    def items(self):
        videos = models.Video.objects.filter(
            site=self.sitelocation.site,
            last_featured__isnull=False,
            status=models.VIDEO_STATUS_ACTIVE)
        videos = videos.order_by(
            '-last_featured', '-when_approved','-when_submitted')
        return videos[:LOCALTV_FEED_LENGTH]


class PopularVideosFeed(BaseVideosFeed):
    def link(self):
        return reverse('localtv_list_popular')

    def items(self):
        videos = models.Video.objects.popular_since(
            datetime.timedelta(days=7), self.sitelocation,
            status=models.VIDEO_STATUS_ACTIVE)
        return videos[:LOCALTV_FEED_LENGTH]

    def title(self):
        return "%s: %s" % (
            self.sitelocation.site.name, _('Popular Videos'))


class CategoryVideosFeed(BaseVideosFeed):
    def get_object(self, bits):
        return models.Category.objects.get(site=self.sitelocation.site,
                                           slug=bits[0])

    def link(self, category):
        return category.get_absolute_url()

    def items(self, category):
        videos = category.video_set.filter(
            status=models.VIDEO_STATUS_ACTIVE)
        return videos[:LOCALTV_FEED_LENGTH]

    def title(self, category):
        return "%s: %s" % (
            self.sitelocation.site.name, _('Category: %s') % category.name)

class AuthorVideosFeed(BaseVideosFeed):
    def get_object(self, bits):
        return User.objects.get(pk=bits[0])

    def link(self, author):
        return reverse('localtv_author', args=[author.pk])

    def items(self, author):
        videos = models.Video.objects.filter(
            Q(authors=author) | Q(user=author),
            site=self.sitelocation.site,
            status=models.VIDEO_STATUS_ACTIVE).distinct()
        return videos[:LOCALTV_FEED_LENGTH]

    def title(self, author):
        return "%s: %s" % (
            self.sitelocation.site.name,
            _('Author: %s') % author.get_full_name())

class TagVideosFeed(BaseVideosFeed):
    def get_object(self, bits):
        return Tag.objects.get(name=bits[0])

    def link(self, tag):
        return reverse('localtv_list_tag', args=[tag.name])

    def items(self, tag):
        videos = models.Video.tagged.with_all(tag).filter(
            site=self.sitelocation.site,
            status=models.VIDEO_STATUS_ACTIVE)
        return videos[:LOCALTV_FEED_LENGTH]

    def title(self, tag):
        return "%s: %s" % (
            self.sitelocation.site.name, _('Tag: %s') % tag.name)

class SearchVideosFeed(BaseVideosFeed):
    def get_object(self, bits):
        return bits[0]

    def link(self, search):
        return reverse('localtv_search') + '?' + urllib.urlencode(
            {'q': search})

    def items(self, search):
        form = haystack.forms.ModelSearchForm({'q': search}, load_all=True)
        if not form.is_valid():
            raise FeedDoesNotExist(search)
        results = form.search()
        return [result.object for result in results[:LOCALTV_FEED_LENGTH]
                if result]

    def title(self, search):
        return "%s: %s" % (
            self.sitelocation.site.name, _('Search: %s') % search)


new = feed_view(NewVideosFeed)
featured = feed_view(FeaturedVideosFeed)
popular = feed_view(PopularVideosFeed)
category = feed_view(CategoryVideosFeed)
author = feed_view(AuthorVideosFeed)
tag = feed_view(TagVideosFeed)
search = feed_view(SearchVideosFeed)