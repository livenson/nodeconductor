User list
---------

User list is available to all authenticated users. To get a list, issue authenticated GET request against **/api/users/**.

User list supports several filters. All filters are set in HTTP query section.
Field filters (all of the filters to case insensitive partial matching) are:

- ?full_name=
- ?native_name=
- ?organization=
- ?email=
- ?phone_number=
- ?description=
- ?job_title=
- ?username=
- ?project=
- ?project_group=

In addition, several custom filters are supported:

- ?current - filters out user making a request. Useful for getting information about a currently logged in user.
- ?civil_number=XXX - filters out users with a specified civil number
- ?is_active=True|False - show only active (non-active) users
- ?potential - shows users that have common connections to the customers and are potential collaborators
- ?potential_customer=<Customer UUID> - optionally filter potential users by customer UUID

Ordering is supported by the fields below. Descending sorting can be achieved through prefixing
field name with a dash (**-**).

- ?o=full_name
- ?o=native_name
- ?o=organization
- ?o=email
- ?o=phone_number
- ?o=description'
- ?o=job_title
- ?o=username
- ?o=active


Creating a user
---------------

The user can be created either through automated process on login with SAML token, or through a REST call by a user
with staff privilege.

Example of a creation request is below.

.. code-block:: http

    POST /api/users/ HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com
    {
        "username": "sample-user",
        "full_name": "full name",
        "native_name": "täisnimi",
        "job_title": "senior cleaning manager",
        "email": "example@example.com",
        "civil_number": "12121212",
        "phone_number": "",
        "description": "",
        "organization": "",
    }

Updating a user
---------------

User fields can be updated by account owner or user with staff privilege (is_staff=True).
Following user fields can be updated:

- organization
- full_name
- native_name
- job_title
- organization
- phone_number
- email

Can be done by PUTing a new data to the user URI, i.e. **/api/users/<UUID>/** by staff user or account owner.
Valid request example (token is user specific):

.. code-block:: http

    PUT /api/users/e0c058d06864441fb4f1c40dee5dd4fd/ HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com

    {
        "email": "example@example.com",
        "organization": "Bells organization",
    }

Changing password
-----------------

To change a user password, submit a POST request to the user's RPC url, specifying new password
by staff user or account owner.

Password validation
^^^^^^^^^^^^^^^^^^^

Password is expected to be at least 7 symbols long and contain at least one number and at least one lower or upper case.

Example of a valid request:

.. code-block:: http

    POST /api/users/e0c058d06864441fb4f1c40dee5dd4fd/password/ HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com

    {
        "password": "nQvqHzeP123",
    }
