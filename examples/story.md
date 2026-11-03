As a workspace admin, I want to invite an external contractor with view-only access to a single workspace,
so that I can share reports without exposing other teams' data.

Today admins create full accounts for contractors and then remove permissions by hand, which takes
about 20 minutes per person and is easy to get wrong.

Acceptance criteria
- Given I am a workspace admin, when I invite a guest by email, then they can only see the workspace I chose.
- Given a guest account, when they open any other workspace URL, then they get an access-denied page.
- Guest invites expire after 30 days unless renewed.

Success metric: reduce time to onboard a contractor from about 20 minutes to under 2, measured over the first month.
