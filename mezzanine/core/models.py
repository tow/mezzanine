
from datetime import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.base import ModelBase
from django.template.defaultfilters import slugify, truncatewords_html
from django.utils.translation import ugettext, ugettext_lazy as _

from mezzanine.core.fields import HtmlField
from mezzanine.core.managers import DisplayableManager, KeywordManager
from mezzanine.settings import CONTENT_STATUS_CHOICES, CONTENT_STATUS_DRAFT
from mezzanine.utils import base_concrete_model


class Slugged(models.Model):
    """
    Abstract model that handles auto-generating slugs.
    """

    title = models.CharField(_("Title"), max_length=100)
    slug = models.CharField(_("URL"), max_length=100, blank=True, null=True)

    class Meta:
        abstract = True
        ordering = ("title",)

    def __unicode__(self):
        return self.title

    def save(self, *args, **kwargs):
        """
        Create a unique slug from the title by appending an index.
        """
        if not self.slug:
            # For custom content types, use the ``Page`` instance for slug
            # lookup.
            concrete_model = base_concrete_model(Slugged, self)
            self.slug = self.get_slug()
            i = 0
            while True:
                if i > 0:
                    if i > 1:
                        self.slug = self.slug.rsplit("-", 1)[0]
                    self.slug = "%s-%s" % (self.slug, i)
                qs = concrete_model.objects.all()
                if self.id is not None:
                    qs = qs.exclude(id=self.id)
                try:
                    qs.get(slug=self.slug)
                except ObjectDoesNotExist:
                    break
                i += 1
        super(Slugged, self).save(*args, **kwargs)

    def natural_key(self):
        return (self.slug,)

    def get_slug(self):
        return slugify(self.title)

class Displayable(Slugged):
    """
    Abstract model that provides features of a visible page on the website
    such as publishing fields and meta data.
    """

    status = models.IntegerField(_("Status"),
        choices=CONTENT_STATUS_CHOICES, default=CONTENT_STATUS_DRAFT)
    publish_date = models.DateTimeField(_("Published from"), 
        help_text=_("With published selected, won't be shown until this time"),
        blank=True, null=True)
    expiry_date = models.DateTimeField(_("Expires on"), 
        help_text=_("With published selected, won't be shown after this time"),
        blank=True, null=True)
    description = HtmlField(_("Description"), blank=True)
    keywords = models.ManyToManyField("Keyword", verbose_name=_("Keywords"),
        blank=True)
    _keywords = models.CharField(max_length=500, editable=False)
    short_url = models.URLField(blank=True, null=True)

    objects = DisplayableManager()
    search_fields = {"_keywords": 10, "title": 5}

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """
        Create the description from the content if none given.
        """
        if self.publish_date is None:
            # publish_date will be blank when a blog post is created from the
            # quick blog form in the admin dashboard.
            self.publish_date = datetime.now()
        if not self.description:
            for field_type in (HtmlField, models.TextField):
                if not self.description:
                    for field in self._meta.fields:
                        if isinstance(field, field_type):
                            self.description = getattr(self, field.name)
                            if self.description:
                                break
            if not self.description:
                self.description = self.title
            for end in ("</p>", "<br />", "\n", ". "):
                if end in self.description:
                    self.description = self.description.split(end)[0] + end
                    break
            else:
                self.description = truncatewords_html(self.description, 100)
        super(Displayable, self).save(*args, **kwargs)

    def set_searchable_keywords(self):
        """
        Stores the keywords as a single string into the ``_keywords`` field
        for convenient access when searching.
        """
        self._keywords = " ".join([kw.title for kw in self.keywords.all()])
        self.save()

    def admin_link(self):
        return "<a href='%s'>%s</a>" % (self.get_absolute_url(),
            ugettext("View on site"))
    admin_link.allow_tags = True
    admin_link.short_description = ""


class Content(models.Model):
    """
    Provides a HTML field for manging general content and making it searchable.
    """

    content = HtmlField(_("Content"))

    search_fields = ("content",)

    class Meta:
        abstract = True


class OrderableBase(ModelBase):
    """
    Checks for ``order_with_respect_to`` on the model's inner ``Meta`` class
    and if found, copies it to a custom attribute and deletes it since it
    will cause errors when used with ``ForeignKey("self")``. Also creates the
    ``ordering`` attribute on the ``Meta`` class if not yet provided.
    """

    def __new__(cls, name, bases, attrs):
        if "Meta" not in attrs:

            class Meta:
                pass
            attrs["Meta"] = Meta
        if hasattr(attrs["Meta"], "order_with_respect_to"):
            attrs["order_with_respect_to"] = attrs["Meta"].order_with_respect_to
            del attrs["Meta"].order_with_respect_to
        if not hasattr(attrs["Meta"], "ordering"):
            setattr(attrs["Meta"], "ordering", ("_order",))
        return super(OrderableBase, cls).__new__(cls, name, bases, attrs)


class Orderable(models.Model):
    """
    Abstract model that provides a custom ordering integer field similar to
    using Meta's ``order_with_respect_to``, since to date (Django 1.2) this
    doesn't work with ``ForeignKey("self")``. We may also want this feature
    for models that aren't ordered with respect to a particular field.
    """

    __metaclass__ = OrderableBase

    _order = models.IntegerField(_("Order"), null=True)

    class Meta:
        abstract = True

    def with_respect_to(self):
        """
        Returns a dict to use as a filter for ordering operations containing
        the original ``Meta.order_with_respect_to`` value if provided.
        """
        try:
            field = self.order_with_respect_to
            return {field: getattr(self, field)}
        except AttributeError:
            return {}

    def save(self, *args, **kwargs):
        """
        Set the initial ordering value.
        """
        if self._order is None:
            lookup = self.with_respect_to()
            concrete_model = base_concrete_model(Orderable, self)
            self._order = concrete_model.objects.filter(**lookup).count()
        super(Orderable, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Update the ordering values for siblings.
        """
        lookup = self.with_respect_to()
        lookup["_order__gte"] = self._order
        concrete_model = base_concrete_model(Orderable, self)
        after = concrete_model.objects.filter(**lookup)
        after.update(_order=models.F("_order") - 1)
        super(Orderable, self).delete(*args, **kwargs)


class Ownable(models.Model):
    """
    Abstract model that provides ownership of an object for a user.
    """

    user = models.ForeignKey("auth.User", verbose_name=_("Author"),
        related_name="%(class)ss")

    class Meta:
        abstract = True

    def is_editable(self, request):
        """
        Restrict in-line editing to the objects's owner and superusers.
        """
        return request.user.is_superuser or request.user.id == self.user_id


class Keyword(Slugged):
    """
    Keywords/tags which are managed via a custom Javascript based widget in the
    admin.
    """

    objects = KeywordManager()

    class Meta:
        verbose_name = "Keyword"
        verbose_name_plural = "Keywords"


from south.modelsinspector import add_introspection_rules
add_introspection_rules([], ["^mezzanine\.core\.fields\.HtmlField"])
