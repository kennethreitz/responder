from pydantic import BaseModel


class Item(BaseModel):
    name: str
    description: str = None
    price: float
    tax: float = None

from responder import API

api = API()


@api.route("/user")
async def create_user(req, resp, *, user: User):
    # `user` is automatically parsed and validated against the User Pydantic model
    # Perform operations with the validated `user` object
    ...
    def create_user(user: User):
        # Create a user in the database
        ...
        return user

    # Return a JSON response
    resp.media = {"message": "User created successfully", "user": user.dict()}
    resp.status_code = api.status_codes.HTTP_201
