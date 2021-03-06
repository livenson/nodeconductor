import base64

from django.contrib.auth import authenticate
from django.core import validators
from rest_framework import serializers
from rest_framework.fields import Field

from nodeconductor.core.signals import pre_serializer_fields
from nodeconductor.structure.filters import filter_queryset_for_user

validate_ipv4_address_within_list = validators.RegexValidator(
    validators.ipv4_re, 'Enter a list of valid IPv4 addresses.',
    'invalid')


class AuthTokenSerializer(serializers.Serializer):
    """
    Api token serializer loosely based on DRF's default AuthTokenSerializer,
    but with the response text and aligned with BasicAuthentication behavior.
    """
    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True)

    def validate(self, attrs):
        # Since the fields are both required and non-blank
        # and field-validation is performed before object-level validation
        # it is safe to assume these dict keys present.
        username = attrs['username']
        password = attrs['password']

        user = authenticate(username=username, password=password)

        if not user:
            raise serializers.ValidationError('Invalid username/password')

        if not user.is_active:
            raise serializers.ValidationError('User account is disabled')
        attrs['user'] = user

        return attrs


class Base64Field(serializers.CharField):
    def from_native(self, value):
        value = super(Base64Field, self).from_native(value)
        try:
            return base64.b64decode(value)
        except TypeError:
            raise serializers.ValidationError("Enter valid Base64 encoded string.")

    def to_native(self, value):
        value = super(Base64Field, self).to_native(value)
        return base64.b64encode(value)


class IPsField(serializers.CharField):
    def to_native(self, value):
        value = super(IPsField, self).to_native(value)
        if value is None:
            return []
        else:
            return [value]

    def from_native(self, value):
        if value in validators.EMPTY_VALUES:
            return None

        if not isinstance(value, (list, tuple)):
            raise validators.ValidationError('Enter a list of valid IPv4 addresses.')

        value_count = len(value)
        if value_count > 1:
            raise validators.ValidationError('Only one ip address is supported.')
        elif value_count == 1:
            value = value[0]
            validate_ipv4_address_within_list(value)
        else:
            value = None

        return value


class Saml2ResponseSerializer(serializers.Serializer):
    saml2response = Base64Field(required=True)


class PermissionFieldFilteringMixin(object):
    """
    Mixin allowing to filter related fields.

    In order to constrain the list of entities that can be used
    as a value for the field:

    1. Make sure that the entity in question has corresponding
       Permission class defined.

    2. Implement `get_filtered_field_names()` method
       in the class that this mixin is mixed into and return
       the field in question from that method.
    """
    def get_fields(self):
        fields = super(PermissionFieldFilteringMixin, self).get_fields()

        try:
            request = self.context['view'].request
            user = request.user
        except (KeyError, AttributeError):
            return fields

        for field_name in self.get_filtered_field_names():
            fields[field_name].queryset = filter_queryset_for_user(
                fields[field_name].queryset, user)

        return fields

    def get_filtered_field_names(self):
        raise NotImplementedError(
            'Implement get_filtered_field_names() '
            'to return list of filtered fields')


class RelatedResourcesFieldMixin(object):
    """
    Mixin that adds fields describing related resources.

    For related resource Foo two fields are added:

    1. `foo` containing a URL to the Foo resource
    2. `foo_name` containing the name of the Foo resource
    3. `foo_uuid` containing the uuid of the Foo resource

    In order to add related resource fields:

    1. Inherit from `RelatedResourcesFieldMixin`.

    2. Implement `get_related_paths()` method
       and return paths to related resources
       from the current resource.
    """
    def get_default_fields(self):
        fields = super(RelatedResourcesFieldMixin, self).get_default_fields()

        for path in self.get_related_paths():
            path_components = path.split('.')
            entity_name = path_components[-1]

            fields[entity_name] = serializers.HyperlinkedRelatedField(
                source=path,
                view_name='{0}-detail'.format(entity_name),
                lookup_field='uuid',
                read_only=len(path_components) > 1,
            )

            fields['{0}_name'.format(entity_name)] = serializers.Field(source='{0}.name'.format(path))
            fields['{0}_uuid'.format(entity_name)] = serializers.Field(source='{0}.uuid'.format(path))

        return fields

    def get_related_paths(self):
        raise NotImplementedError(
            'Implement get_paths() to return list of filtered fields')


class BasicInfoSerializer(serializers.HyperlinkedModelSerializer):
    class Meta(object):
        fields = ('url', 'name')
        lookup_field = 'uuid'


class UnboundSerializerMethodField(Field):
    """
    A field that gets its value by calling a provided filter callback.
    """

    def __init__(self, filter_function, *args, **kwargs):
        self.filter_function = filter_function
        super(UnboundSerializerMethodField, self).__init__(*args, **kwargs)

    def field_to_native(self, obj, field_name):
        try:
            request = self.context['request']
        except KeyError:
            return self.to_native(obj)

        value = self.filter_function(obj, request)
        return self.to_native(value)


class CollectedFieldsMixin(object):
    """
    A mixin that allows serializer to send a signal for modifying (e.g. adding) fields into the rendering.
    Useful when you want to enrich output with fields coming from modules that are imported later
    or from plugins.

    Handler should bind to 'pre_serializer_fields' signal.

    Example of signal handler implementation:

        def get_customer_clouds(obj, request):
            customer_clouds = obj.clouds.all()
            try:
                user = request.user
                customer_clouds = filter_queryset_for_user(customer_clouds, user)
            except AttributeError:
                pass

            from nodeconductor.iaas.serializers import BasicCloudSerializer
            serializer_instance = BasicCloudSerializer(customer_clouds, context={'request': request})

            return serializer_instance.data

        # @receiver(pre_serializer_fields, sender=CustomerSerializer)  # Django 1.7
        @receiver(pre_serializer_fields)
        def add_clouds_to_customer(sender, fields, **kwargs):
            # Note: importing here to avoid circular import hell
            from nodeconductor.structure.serializers import CustomerSerializer
            if sender is not CustomerSerializer:
                return

            fields['clouds'] = UnboundSerializerMethodField(get_customer_clouds)

    """

    def get_fields(self):
        fields = super(CollectedFieldsMixin, self).get_fields()
        pre_serializer_fields.send(sender=self.__class__, fields=fields)
        return fields
