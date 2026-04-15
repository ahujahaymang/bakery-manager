"""
Unit tests for DisambiguationHelper.

Tests the disambiguation logic for customers and recipes.
"""

import pytest
from unittest.mock import Mock
from app.helpers.disambiguation_helper import DisambiguationHelper


class TestDisambiguationHelper:
    """Test suite for DisambiguationHelper class."""
    
    def test_create_customer_options(self):
        """Test creating customer options from customer objects."""
        # Arrange
        customer1 = Mock()
        customer1.name = "Priya Sharma"
        customer1.phone = "9876543210"
        customer1.customer_id = "uuid-1"
        
        customer2 = Mock()
        customer2.name = "Priya Kumar"
        customer2.phone = "9876543211"
        customer2.customer_id = "uuid-2"
        
        customers = [customer1, customer2]
        
        # Act
        options = DisambiguationHelper.create_customer_options(customers)
        
        # Assert
        assert len(options) == 2
        assert options[0] == {'name': 'Priya Sharma', 'phone': '9876543210', 'id': 'uuid-1'}
        assert options[1] == {'name': 'Priya Kumar', 'phone': '9876543211', 'id': 'uuid-2'}
    
    def test_create_customer_prompt(self):
        """Test creating customer disambiguation prompt."""
        # Arrange
        customer1 = Mock()
        customer1.name = "Priya Sharma"
        customer1.phone = "9876543210"
        
        customer2 = Mock()
        customer2.name = "Priya Kumar"
        customer2.phone = "9876543211"
        
        customers = [customer1, customer2]
        
        # Act
        prompt = DisambiguationHelper.create_customer_prompt("Priya", customers)
        
        # Assert
        assert "multiple customers" in prompt.lower()
        assert "Priya Sharma" in prompt
        assert "9876543210" in prompt
        assert "Priya Kumar" in prompt
        assert "9876543211" in prompt
        assert "1." in prompt
        assert "2." in prompt
    
    def test_create_recipe_options(self):
        """Test creating recipe options from recipe objects."""
        # Arrange
        recipe1 = Mock()
        recipe1.name = "Chocolate Cake"
        recipe1.recipe_id = "uuid-1"
        
        recipe2 = Mock()
        recipe2.name = "Chocolate Cupcake"
        recipe2.recipe_id = "uuid-2"
        
        recipes = [recipe1, recipe2]
        
        # Act
        options = DisambiguationHelper.create_recipe_options(recipes)
        
        # Assert
        assert len(options) == 2
        assert options[0] == {'name': 'Chocolate Cake', 'id': 'uuid-1'}
        assert options[1] == {'name': 'Chocolate Cupcake', 'id': 'uuid-2'}
    
    def test_create_recipe_prompt(self):
        """Test creating recipe disambiguation prompt."""
        # Arrange
        recipe1 = Mock()
        recipe1.name = "Chocolate Cake"
        
        recipe2 = Mock()
        recipe2.name = "Chocolate Cupcake"
        
        recipes = [recipe1, recipe2]
        
        # Act
        prompt = DisambiguationHelper.create_recipe_prompt("chocolate", recipes)
        
        # Assert
        assert "multiple recipes" in prompt.lower()
        assert "Chocolate Cake" in prompt
        assert "Chocolate Cupcake" in prompt
        assert "1." in prompt
        assert "2." in prompt
    
    def test_parse_customer_choice_by_number(self):
        """Test parsing customer choice by number."""
        # Arrange
        options = [
            {'name': 'Priya Sharma', 'phone': '9876543210', 'id': 'uuid-1'},
            {'name': 'Priya Kumar', 'phone': '9876543211', 'id': 'uuid-2'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_customer_choice("1", options)
        
        # Assert
        assert result == options[0]
    
    def test_parse_customer_choice_by_phone(self):
        """Test parsing customer choice by phone number."""
        # Arrange
        options = [
            {'name': 'Priya Sharma', 'phone': '9876543210', 'id': 'uuid-1'},
            {'name': 'Priya Kumar', 'phone': '9876543211', 'id': 'uuid-2'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_customer_choice("9876543211", options)
        
        # Assert
        assert result == options[1]
    
    def test_parse_customer_choice_invalid(self):
        """Test parsing invalid customer choice."""
        # Arrange
        options = [
            {'name': 'Priya Sharma', 'phone': '9876543210', 'id': 'uuid-1'},
            {'name': 'Priya Kumar', 'phone': '9876543211', 'id': 'uuid-2'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_customer_choice("invalid", options)
        
        # Assert
        assert result is None
    
    def test_parse_customer_choice_out_of_range(self):
        """Test parsing customer choice with number out of range."""
        # Arrange
        options = [
            {'name': 'Priya Sharma', 'phone': '9876543210', 'id': 'uuid-1'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_customer_choice("5", options)
        
        # Assert
        assert result is None
    
    def test_parse_recipe_choice_by_number(self):
        """Test parsing recipe choice by number."""
        # Arrange
        options = [
            {'name': 'Chocolate Cake', 'id': 'uuid-1'},
            {'name': 'Chocolate Cupcake', 'id': 'uuid-2'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_recipe_choice("2", options)
        
        # Assert
        assert result == options[1]
    
    def test_parse_recipe_choice_by_name(self):
        """Test parsing recipe choice by name."""
        # Arrange
        options = [
            {'name': 'Chocolate Cake', 'id': 'uuid-1'},
            {'name': 'Chocolate Cupcake', 'id': 'uuid-2'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_recipe_choice("cupcake", options)
        
        # Assert
        assert result == options[1]
    
    def test_parse_recipe_choice_invalid(self):
        """Test parsing invalid recipe choice."""
        # Arrange
        options = [
            {'name': 'Chocolate Cake', 'id': 'uuid-1'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_recipe_choice("vanilla", options)
        
        # Assert
        assert result is None
    
    def test_parse_customer_choice_case_insensitive(self):
        """Test that customer choice parsing is case insensitive."""
        # Arrange
        options = [
            {'name': 'Priya Sharma', 'phone': '9876543210', 'id': 'uuid-1'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_customer_choice("9876543210", options)
        
        # Assert
        assert result == options[0]
    
    def test_parse_recipe_choice_partial_match(self):
        """Test that recipe choice supports partial name matching."""
        # Arrange
        options = [
            {'name': 'Chocolate Cake', 'id': 'uuid-1'},
            {'name': 'Vanilla Cake', 'id': 'uuid-2'}
        ]
        
        # Act
        result = DisambiguationHelper.parse_recipe_choice("choco", options)
        
        # Assert
        assert result == options[0]
