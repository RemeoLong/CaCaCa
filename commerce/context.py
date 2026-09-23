def cart_summary(request):
    cart = request.session.get('cart', {})
    count = sum(q for q in cart.values() if type(q) is int and 0 < q <= 20) if isinstance(cart, dict) else 0
    return {'cart_count': count}
