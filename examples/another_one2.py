import responder
from pydantic import BaseModel, EmailStr


@api.schema("UserIn")
class UserInSchema(BaseModel):
    name: str
    email: EmailStr
    password: str


@api.schema("UserIn")
class UserOutSchema(BaseModel):
    id: int
    name: str
    email: EmailStr


class User(db.Model):  # ORM model maybe `SQLAlchemy`
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), index=True)
    email = db.Column(db.String(64), unique=True, index=True)
    password_hash = db.Column(db.String(128))


api = responder.API()



@api.route("/items/", request_model=ItemRequest, response_model=ItemResponse)
async def create_item(req, resp, *, parsed_request: ItemRequest):
    # Use parsed_request directly in your handler
    ...

    

@api.route("/items/", request_model=ItemRequest, response_model=ItemResponse)


@api.route("/users")
@input(UserInSchema)    # A decorator called `input` or `in` to validate incoming schema
@output(UserOutSchema, 201)  # A decorator called `output` or `out` to validate outgoing schema
@expect({404: "User not found", 409: "User already followed."})  # A decorator called `expect` for documentation
def new(req, res, *, args):  # args is already the validated UserInSchema dictionary data
    """Register a new user"""      # This doctstring will appear in the openapi documentation
    user = User(**args)
    db.session.add(user)
    db.session.commit()
    
    # some logic that raises 409
    resp.status_code = api.status_codes.HTTP_409 

    return user   # Just return the model whose data will be serialized using the output decorator.
