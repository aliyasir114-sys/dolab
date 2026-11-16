from datetime import date, timedelta

SHIRT = {"name": "Classic white shirt", "category": "shirt", "primary_color": "white"}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- account ---------------------------------------------------------------


def test_register_login_and_me(client, signup):
    headers = signup("Ali@Example.com")
    me = client.get("/me", headers=headers).json()
    assert me["email"] == "ali@example.com"
    assert me["locale"] == "ar"
    assert "password_hash" not in me


def test_duplicate_email_is_rejected(client, signup):
    signup("a@example.com")
    again = client.post("/auth/register", json={"email": "A@example.com", "password": "another-pass"})
    assert again.status_code == 409


def test_weak_password_and_bad_email_are_rejected(client):
    assert client.post("/auth/register", json={"email": "a@example.com", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"email": "nope", "password": "long-enough"}).status_code == 422


def test_wrong_password_and_unknown_email_look_the_same(client, signup):
    signup("a@example.com")
    wrong = client.post("/auth/login", json={"email": "a@example.com", "password": "wrong-password"})
    unknown = client.post("/auth/login", json={"email": "b@example.com", "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_endpoints_require_a_valid_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/items").status_code == 401
    assert client.get("/items", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_profile_is_optional_and_validated(client, signup):
    headers = signup()
    assert client.get("/me/profile", headers=headers).json()["height_cm"] is None
    saved = client.put("/me/profile", json={"height_cm": 175, "usual_size": "L"}, headers=headers)
    assert saved.status_code == 200
    assert client.get("/me/profile", headers=headers).json() == {
        "height_cm": 175.0,
        "weight_kg": None,
        "usual_size": "L",
        "body_shape": None,
        "face_shape": None,
    }
    assert client.put("/me/profile", json={"height_cm": 900}, headers=headers).status_code == 422


# --- wardrobe --------------------------------------------------------------


def test_item_crud(client, signup):
    headers = signup()
    created = client.post("/items", json=SHIRT, headers=headers)
    assert created.status_code == 201
    item = created.json()
    assert item["status"] == "available"
    assert item["wear_count"] == 0
    assert item["cost_per_wear"] is None

    patched = client.patch(f"/items/{item['id']}", json={"status": "laundry"}, headers=headers).json()
    assert patched["status"] == "laundry"
    assert patched["name"] == SHIRT["name"]

    assert client.delete(f"/items/{item['id']}", headers=headers).status_code == 204
    assert client.get(f"/items/{item['id']}", headers=headers).status_code == 404


def test_patch_null_clears_only_nullable_fields(client, signup):
    headers = signup()
    item = client.post("/items", json=SHIRT, headers=headers).json()
    patched = client.patch(f"/items/{item['id']}", json={"primary_color": None, "name": None}, headers=headers).json()
    assert patched["primary_color"] is None
    assert patched["name"] == SHIRT["name"]


def test_invalid_status_is_rejected(client, signup):
    headers = signup()
    assert client.post("/items", json={**SHIRT, "status": "burned"}, headers=headers).status_code == 422


def test_list_filters_by_status(client, signup):
    headers = signup()
    client.post("/items", json=SHIRT, headers=headers)
    client.post("/items", json={"name": "Grey jacket", "category": "jacket", "status": "laundry"}, headers=headers)
    assert len(client.get("/items", headers=headers).json()) == 2
    available = client.get("/items", params={"status": "available"}, headers=headers).json()
    assert [i["name"] for i in available] == [SHIRT["name"]]


def test_users_cannot_see_or_change_each_others_items(client, signup):
    owner = signup("owner@example.com")
    other = signup("other@example.com")
    item_id = client.post("/items", json=SHIRT, headers=owner).json()["id"]
    assert client.get("/items", headers=other).json() == []
    assert client.get(f"/items/{item_id}", headers=other).status_code == 404
    assert client.patch(f"/items/{item_id}", json={"name": "mine now"}, headers=other).status_code == 404
    assert client.delete(f"/items/{item_id}", headers=other).status_code == 404
    assert client.post(f"/items/{item_id}/wear", headers=other).status_code == 404
    assert client.get(f"/items/{item_id}", headers=owner).json()["name"] == SHIRT["name"]


def test_wearing_counts_and_cost_per_wear(client, signup):
    headers = signup()
    body = {**SHIRT, "purchase_price": "40.00", "currency": "usd"}
    item_id = client.post("/items", json=body, headers=headers).json()["id"]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client.post(f"/items/{item_id}/wear", json={"worn_on": yesterday}, headers=headers)
    worn = client.post(f"/items/{item_id}/wear", headers=headers).json()
    assert worn["wear_count"] == 2
    assert worn["last_worn_at"] == date.today().isoformat()
    assert worn["currency"] == "USD"
    assert worn["cost_per_wear"] == "20.00"


def test_only_available_items_can_be_worn(client, signup):
    headers = signup()
    item_id = client.post("/items", json={**SHIRT, "status": "laundry"}, headers=headers).json()["id"]
    assert client.post(f"/items/{item_id}/wear", headers=headers).status_code == 409


def test_wear_date_cannot_be_in_the_future(client, signup):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.post(f"/items/{item_id}/wear", json={"worn_on": tomorrow}, headers=headers).status_code == 422


# --- donations -------------------------------------------------------------


def test_donating_marks_items_donated(client, signup):
    headers = signup()
    first = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    second = client.post("/items", json={"name": "Grey jacket", "category": "jacket"}, headers=headers).json()["id"]
    donation = client.post(
        "/donations", json={"item_ids": [first, second], "recipient_type": "charity"}, headers=headers
    )
    assert donation.status_code == 201
    assert donation.json()["item_ids"] == sorted([first, second])
    assert client.get(f"/items/{first}", headers=headers).json()["status"] == "donated"
    assert client.post(f"/items/{first}/wear", headers=headers).status_code == 409
    assert len(client.get("/donations", headers=headers).json()) == 1


def test_cannot_donate_twice_or_donate_someone_elses_item(client, signup):
    owner = signup("owner@example.com")
    other = signup("other@example.com")
    item_id = client.post("/items", json=SHIRT, headers=owner).json()["id"]
    body = {"item_ids": [item_id], "recipient_type": "person", "recipient_name": "A friend"}
    assert client.post("/donations", json=body, headers=other).status_code == 404
    assert client.post("/donations", json=body, headers=owner).status_code == 201
    assert client.post("/donations", json=body, headers=owner).status_code == 409


# --- dashboard and deletion ------------------------------------------------


def test_dashboard_numbers(client, signup):
    headers = signup()
    shirt = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    client.post("/items", json={"name": "Black trousers", "category": "trousers", "primary_color": "black"}, headers=headers)
    jacket = client.post(
        "/items", json={"name": "Grey jacket", "category": "jacket", "primary_color": "grey"}, headers=headers
    ).json()["id"]
    client.post(f"/items/{shirt}/wear", headers=headers)
    client.post("/donations", json={"item_ids": [jacket], "recipient_type": "charity"}, headers=headers)

    data = client.get("/me/dashboard", headers=headers).json()
    assert data["total_items"] == 3
    assert data["active_items"] == 2
    assert data["by_status"] == {"available": 2, "donated": 1}
    assert data["colors"] == {"white": 1, "black": 1}
    assert data["worn_this_month"] == 1
    assert data["usage_ratio"] == 0.5


def test_empty_dashboard(client, signup):
    data = client.get("/me/dashboard", headers=signup()).json()
    assert data["total_items"] == 0
    assert data["usage_ratio"] is None


def test_deleting_the_account_removes_everything(client, signup):
    headers = signup("gone@example.com")
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    client.post(f"/items/{item_id}/wear", headers=headers)
    client.put("/me/profile", json={"height_cm": 170}, headers=headers)
    client.post("/donations", json={"item_ids": [item_id], "recipient_type": "charity"}, headers=headers)

    assert client.delete("/me", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401
    # The email is free again and the new account starts empty.
    fresh = signup("gone@example.com")
    assert client.get("/items", headers=fresh).json() == []
    assert client.get("/donations", headers=fresh).json() == []
