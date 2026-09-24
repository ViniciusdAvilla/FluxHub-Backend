from app.database.repositories.products import ProductRepository


class ProductService:

    def __init__(
        self,
        repository: ProductRepository,
    ):
        self.repository = repository


    def get_products(self):

        return self.repository.get_all()


    def count_products(self):

        return self.repository.count_products()


    def get_top_discount(self):

        return self.repository.get_top_discount()


    def get_top_stock(self):

        return self.repository.get_top_stock()